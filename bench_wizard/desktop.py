"""Run the wizard like a desktop app: a launcher icon and its own window."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
    return f"""#!/bin/sh
for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  [ -x "$brew" ] && eval "$("$brew" shellenv)" && break
done
exec "{root}/start.sh" --app
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


def _mac_icon(svg, icns):
    """Best effort: render the SVG with Quick Look and convert it to .icns. No icon if that fails."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["qlmanage", "-t", "-s", "512", "-o", tmp, svg], capture_output=True, timeout=30)
            png = os.path.join(tmp, os.path.basename(svg) + ".png")
            if os.path.exists(png):
                subprocess.run(["sips", "-s", "format", "icns", png, "--out", icns], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


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
    _mac_icon(os.path.join(root, "bench_wizard/web/icon.svg"), os.path.join(bundle, "Contents/Resources/icon.icns"))
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


def window_command(browser, url, state_root):
    # A profile of its own makes the browser process last exactly as long as the window.
    return [browser, f"--app={url}", f"--user-data-dir={os.path.join(state_root, 'window-profile')}",
            "--class=bench-wizard", "--no-first-run", "--no-default-browser-check", "--window-size=1320,920"]


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
