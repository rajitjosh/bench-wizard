import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser

from . import catalog, desktop, server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_CATALOG = os.path.expanduser("~/.config/bench-wizard/catalog.json")
STATE_ROOT = os.path.expanduser("~/.local/state/bench-wizard")
INSTANCE = os.path.join(STATE_ROOT, "instance.json")


def open_window(url):
    """Show the wizard in a window of its own. Returns the browser process, or None."""
    browser = desktop.find_browser()
    if not browser:
        webbrowser.open(url)
        return None
    return subprocess.Popen(desktop.window_command(browser, url, STATE_ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(prog="bench-wizard", description="Set up a local Frappe bench.")
    parser.add_argument("--port", type=int, default=0, help="port to listen on (default: any free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--app", action="store_true",
                        help="open in a window of its own and stop when that window is closed")
    parser.add_argument("--install-desktop", action="store_true",
                        help="add Bench Wizard to the applications menu, then exit")
    args = parser.parse_args()

    if args.install_desktop:
        print("Added to the applications menu:", desktop.install(ROOT))
        return

    existing = desktop.running_instance(INSTANCE)
    if existing and args.app:
        open_window(existing)  # a second click on the icon shows the wizard that is already running
        return

    try:
        data = catalog.load(os.path.join(ROOT, "catalog.json"), LOCAL_CATALOG)
    except catalog.CatalogError as exc:
        sys.exit(f"bench-wizard: {exc}")
    app = server.App(data, STATE_ROOT)
    httpd = server.make_server(app, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?token={app.token}"
    desktop.record_instance(INSTANCE, url, os.getpid())
    try:
        window = open_window(url) if args.app else None
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
