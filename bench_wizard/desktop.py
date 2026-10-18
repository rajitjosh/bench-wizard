"""Run the wizard like a desktop app: a launcher icon and its own window."""
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import threading
import time

BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
            "brave-browser")
MAC_BROWSERS = ("Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser")


def entry(root):
    return f"""[Desktop Entry]
Type=Application
Name=Bench Wizard
Comment=Set up a local Frappe bench
Exec="{root}/start.sh" --app
Icon={root}/bench_wizard/web/icon.svg
Terminal=false
Categories=Development;
StartupWMClass=bench-wizard
"""


def mac_launcher(root):
    # Apps started from Finder get a bare PATH; brew shellenv adds Homebrew's.
    # The wizard runs in the background and this script exits at once. While the app's own process is alive,
    # macOS treats a second open (Spotlight, Dock, Finder) as "bring it to the front" and runs nothing, and
    # the wizard has no window of its own to bring forward. Exiting makes each open run this again, which
    # shows the window of the wizard that is already running.
    return f"""#!/bin/sh
for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$brew" ] && eval "$("$brew" shellenv)" && break
done
mkdir -p "$HOME/.local/state/bench-wizard"
"{root}/start.sh" --app </dev/null >>"$HOME/.local/state/bench-wizard/app.log" 2>&1 &
"""


MAC_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Bench Wizard</string>
  <key>CFBundleIdentifier</key><string>local.bench-wizard</string>
  <key>CFBundleExecutable</key><string>bench-wizard</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
"""


def install_mac(root, applications_dir=None):
    """Create ~/Applications/Bench Wizard.app. Returns the bundle's path."""
    bundle = os.path.join(applications_dir or os.path.expanduser("~/Applications"), "Bench Wizard.app")
    os.makedirs(os.path.join(bundle, "Contents/MacOS"), exist_ok=True)
    os.makedirs(os.path.join(bundle, "Contents/Resources"), exist_ok=True)
    with open(os.path.join(bundle, "Contents/Info.plist"), "w") as fh:
        fh.write(MAC_PLIST)
    launcher = os.path.join(bundle, "Contents/MacOS/bench-wizard")
    with open(launcher, "w") as fh:
        fh.write(mac_launcher(root))
    os.chmod(launcher, 0o755)
    # icon.icns is built from web/icon-mac.svg (see README); copying it busts the Finder icon cache.
    shutil.copyfile(os.path.join(root, "bench_wizard/web/icon.icns"), os.path.join(bundle, "Contents/Resources/icon.icns"))
    os.utime(bundle)
    return bundle


def install(root, applications_dir=None, system=None):
    """Add Bench Wizard to the desktop's application menu. Returns the file written."""
    if (system or sys.platform) == "darwin":
        return install_mac(root, applications_dir)
    applications_dir = applications_dir or os.path.expanduser("~/.local/share/applications")
    os.makedirs(applications_dir, exist_ok=True)
    path = os.path.join(applications_dir, "bench-wizard.desktop")
    with open(path, "w") as fh:
        fh.write(entry(root))
    os.chmod(path, 0o755)
    return path


def find_browser(system=None, app_dirs=None):
    """A Chromium-family browser that can show the wizard in a window of its own."""
    override = os.environ.get("BENCH_WIZARD_BROWSER")
    if override:
        return override
    if (system or sys.platform) == "darwin":
        for folder in app_dirs or ("/Applications", os.path.expanduser("~/Applications")):
            for name in MAC_BROWSERS:
                path = os.path.join(folder, f"{name}.app", "Contents", "MacOS", name)
                if os.access(path, os.X_OK):
                    return path
        return None
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def snap_name(browser):
    """The snap a Linux browser runs from (Ubuntu's Chromium is one), else None.

    A snap cannot use hidden folders in the home folder, and the DevTools pipe is not relied on through it.
    """
    if browser.startswith("/snap/bin/"):
        return os.path.basename(browser).split(".")[0]
    try:
        with open(browser, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    found = re.search(rb"/snap/bin/([a-z0-9-]+)", head) if head.startswith(b"#!") else None  # a shim script
    return found.group(1).decode() if found else None


def window_command(browser, url, state_root, devtools=False):
    # A profile of its own makes the browser process last exactly as long as the window.
    snap = snap_name(browser)
    profile = (os.path.expanduser(f"~/snap/{snap}/common/bench-wizard-window-profile") if snap
               else os.path.join(state_root, "window-profile"))
    command = [browser, f"--app={url}", f"--user-data-dir={profile}",
               "--class=bench-wizard", "--no-first-run", "--no-default-browser-check", "--window-size=1320,920"]
    return command + ["--remote-debugging-pipe"] if devtools and not snap else command


class DevToolsError(Exception):
    pass


class DevTools:
    """Chrome's DevTools protocol over --remote-debugging-pipe, for the wizard's own browser process.

    The browser reads commands on its fd 3 and writes replies on its fd 4, each a JSON message ending in
    NUL. A pipe rather than a port, so nothing else on the machine can drive this browser.
    """

    def __init__(self):
        self._child_in, self._to_browser = os.pipe()
        self._from_browser, self._child_out = os.pipe()
        self._lock = threading.Lock()
        self._next_id = 0
        self._buffer = b""

    def child_setup(self):
        """preexec_fn of the browser process: put its ends of the pipes on fds 3 and 4."""
        # Copy them above 4 first, so neither is overwritten before it is moved; dup2 leaves 3 and 4 inheritable.
        ends = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in (self._child_in, self._child_out)]
        os.dup2(ends[0], 3)
        os.dup2(ends[1], 4)

    def started(self):
        """The browser has its ends; close them here so a closed browser reads as end of file."""
        os.close(self._child_in)
        os.close(self._child_out)

    def call(self, method, params=None, timeout=5):
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            os.write(self._to_browser, json.dumps({"id": msg_id, "method": method, "params": params or {}}).encode()
                     + b"\0")
            deadline = time.monotonic() + timeout
            while True:
                while b"\0" in self._buffer:
                    raw, self._buffer = self._buffer.split(b"\0", 1)
                    reply = json.loads(raw)
                    if reply.get("id") == msg_id:
                        if "error" in reply:
                            raise DevToolsError(f"{method}: {reply['error'].get('message', 'failed')}")
                        return reply.get("result", {})
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([self._from_browser], [], [], left)[0]:
                    raise DevToolsError(f"{method}: no reply")
                chunk = os.read(self._from_browser, 65536)
                if not chunk:
                    raise DevToolsError("the browser has quit")
                self._buffer += chunk


def show_page(devtools, url_prefix):
    """Un-minimise the window showing a page under `url_prefix` and bring that tab forward.

    False when no such page is open, for instance after its window was closed.
    """
    try:
        pages = [t for t in devtools.call("Target.getTargets")["targetInfos"]
                 if t.get("type") == "page" and t.get("url", "").startswith(url_prefix)]
        if not pages:
            return False
        target = pages[0]["targetId"]
        window = devtools.call("Browser.getWindowForTarget", {"targetId": target})
        if window.get("bounds", {}).get("windowState") == "minimized":
            devtools.call("Browser.setWindowBounds", {"windowId": window["windowId"],
                                                      "bounds": {"windowState": "normal"}})
        devtools.call("Target.activateTarget", {"targetId": target})
        return True
    except (OSError, ValueError, KeyError, DevToolsError):
        return False


ACTIVATE_JXA = """
ObjC.import('AppKit');
function run(argv) {
  const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(argv[0]));
  return !app.isNil() && app.activateWithOptions(3) ? 'ok' : 'failed';  // all windows, ignoring other apps
}
"""


def activate(pid, run=subprocess.run):
    """Bring the app with process `pid` to the front. macOS only; needs no Apple Events permission."""
    try:
        out = run(["osascript", "-l", "JavaScript", "-e", ACTIVATE_JXA, str(pid)],
                  capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and out.stdout.strip() == "ok"


def reveal(folder, system=None):
    """Show a folder in Finder or the file manager."""
    opener = "open" if (system or sys.platform) == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:  # no xdg-open, as on minimal Linux installs and WSL
        return False
    return True


def record_instance(path, url, pid):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"url": url, "pid": pid}, fh)
    os.chmod(path, 0o600)


def _read(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and "url" in data and "pid" in data else None
    except (OSError, ValueError):
        return None


def running_instance(path):
    """The URL of a wizard that is already running, if there is one."""
    data = _read(path)
    if not data:
        return None
    try:
        os.kill(int(data["pid"]), 0)
    except (OSError, ValueError):
        return None
    return data["url"]


def forget_instance(path, pid):
    data = _read(path)
    if data and data["pid"] == pid:
        os.remove(path)
