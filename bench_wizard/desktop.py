"""Run the wizard like a desktop app: a launcher icon and its own window."""
import json
import os
import shutil

BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
            "brave-browser")


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


def install(root, applications_dir=None):
    """Add Bench Wizard to the desktop's application menu. Returns the file written."""
    applications_dir = applications_dir or os.path.expanduser("~/.local/share/applications")
    os.makedirs(applications_dir, exist_ok=True)
    path = os.path.join(applications_dir, "bench-wizard.desktop")
    with open(path, "w") as fh:
        fh.write(entry(root))
    os.chmod(path, 0o755)
    return path


def find_browser():
    """A Chromium-family browser that can show the wizard in a window of its own."""
    override = os.environ.get("BENCH_WIZARD_BROWSER")
    if override:
        return override
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
