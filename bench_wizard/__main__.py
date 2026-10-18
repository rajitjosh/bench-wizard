import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from urllib.parse import parse_qs, urlsplit

from . import catalog, desktop, server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_CATALOG = os.path.expanduser("~/.config/bench-wizard/catalog.json")
# This machine's own settings, e.g. {"github_ssh_host": "github-work"}; never part of the repository.
SETTINGS = os.path.expanduser("~/.config/bench-wizard/settings.json")
STATE_ROOT = os.path.expanduser("~/.local/state/bench-wizard")
INSTANCE = os.path.join(STATE_ROOT, "instance.json")


def open_window(url, devtools=None):
    """Show the wizard in a window of its own. Returns the browser process, or None."""
    browser = desktop.find_browser()
    if not browser:
        webbrowser.open(url)
        return None
    if devtools is None or desktop.snap_name(browser):
        return subprocess.Popen(desktop.window_command(browser, url, STATE_ROOT),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # close_fds=False keeps fds 3 and 4 that child_setup makes; Python's own descriptors are not inheritable.
    proc = subprocess.Popen(desktop.window_command(browser, url, STATE_ROOT, devtools=True),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=False, preexec_fn=devtools.child_setup)
    devtools.started()
    proc.devtools = devtools
    return proc


def show_existing(url):
    """Ask the running wizard to bring back its window, minimised or not. False when it has none."""
    parts = urlsplit(url)
    try:
        token = parse_qs(parts.query)["token"][0]
        request = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/api/window/show", data=b"{}",
                                         headers={"X-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as resp:
            return json.load(resp).get("shown") is True
    except (OSError, ValueError, KeyError):
        return False


def load_settings(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        sys.exit(f"bench-wizard: cannot read {path}: {exc}")
    if not isinstance(data, dict):
        sys.exit(f"bench-wizard: {path} must hold a JSON object")
    return data


def main():
    parser = argparse.ArgumentParser(prog="bench-wizard", description="Set up a local Frappe bench.")
    parser.add_argument("--port", type=int, default=0,
                        help="port to listen on (default: the first free of 47615-47619, else any free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--app", action="store_true",
                        help="open in a window of its own and stop when that window is closed")
    parser.add_argument("--install-desktop", action="store_true",
                        help="add Bench Wizard to the applications menu, then exit")
    args = parser.parse_args()

    if args.install_desktop:
        print("Added Bench Wizard to your applications:", desktop.install(ROOT))
        return

    existing = desktop.running_instance(INSTANCE)
    if existing and args.app:
        if not show_existing(existing):
            open_window(existing)  # a second click on the icon shows the wizard that is already running
        return

    try:
        data = catalog.load(os.path.join(ROOT, "catalog.json"), LOCAL_CATALOG)
    except catalog.CatalogError as exc:
        sys.exit(f"bench-wizard: {exc}")
    try:
        app = server.App(data, STATE_ROOT, github_ssh_host=load_settings(SETTINGS).get("github_ssh_host"))
    except ValueError as exc:
        sys.exit(f"bench-wizard: {SETTINGS}: {exc}")
    httpd = server.listen(app, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?token={app.token}"
    desktop.record_instance(INSTANCE, url, os.getpid())
    try:
        devtools = desktop.DevTools() if args.app else None
        window = open_window(url, devtools) if args.app else None
        if window is not None and getattr(window, "devtools", None):
            page = url.split("?", 1)[0]
            app.show_window = lambda: desktop.show_page(devtools, page) and (
                sys.platform != "darwin" or desktop.activate(window.pid))
        if window is not None:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            window.wait()
            while app.runner is not None and app.runner.state == "running":
                time.sleep(2)  # closing the window does not interrupt a setup in progress
            httpd.shutdown()
            return
        print(f"Bench Wizard is running at:\n\n  {url}\n\nKeep this terminal open. Press Ctrl+C to stop.",
              flush=True)
        if not args.no_browser and not args.app:
            webbrowser.open(url)
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        if app.runner:
            app.runner.cancel()
    finally:
        desktop.forget_instance(INSTANCE, os.getpid())


if __name__ == "__main__":
    main()
