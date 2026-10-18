import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from bench_wizard import desktop

ROOT = Path(__file__).resolve().parent.parent


class EntryTest(unittest.TestCase):
    def test_entry_launches_app_mode_without_a_terminal(self):
        text = desktop.entry("/opt/bench wizard")
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Name=Bench Wizard", text)
        self.assertIn("Type=Application", text)
        self.assertIn("Terminal=false", text)
        self.assertIn('Exec="/opt/bench wizard/start.sh" --app', text)
        self.assertIn("Icon=/opt/bench wizard/bench_wizard/web/icon.svg", text)

    def test_install_writes_the_entry_and_icon_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = desktop.install(str(ROOT), applications_dir=tmp, system="linux")
            self.assertEqual(path, os.path.join(tmp, "bench-wizard.desktop"))
            self.assertIn("--app", Path(path).read_text())
        self.assertTrue((ROOT / "bench_wizard/web/icon.svg").exists())


class MacTest(unittest.TestCase):
    def test_install_builds_an_app_bundle_that_runs_start_sh(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = desktop.install(str(ROOT), applications_dir=tmp, system="darwin")
            self.assertEqual(bundle, os.path.join(tmp, "Bench Wizard.app"))
            plist = Path(bundle, "Contents/Info.plist").read_text()
            self.assertIn("<string>bench-wizard</string>", plist)
            launcher = Path(bundle, "Contents/MacOS/bench-wizard")
            self.assertTrue(os.access(launcher, os.X_OK))
            script = launcher.read_text()
            self.assertIn(f'"{ROOT}/start.sh" --app </dev/null', script)
            self.assertTrue(script.rstrip().endswith("&"))  # runs in the background so the app process exits
            self.assertNotIn("exec ", script)
            self.assertIn("<key>CFBundleIconFile</key><string>icon</string>", plist)
            self.assertEqual(Path(bundle, "Contents/Resources/icon.icns").read_bytes()[:4], b"icns")
            self.assertIn("brew\" shellenv", launcher.read_text())

    def test_finds_chrome_inside_applications(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(desktop.find_browser("darwin", [tmp]))
            exe = Path(tmp, "Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
            exe.parent.mkdir(parents=True)
            exe.write_text("")
            exe.chmod(0o755)
            self.assertEqual(desktop.find_browser("darwin", [tmp]), str(exe))


class InstanceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "state", "instance.json")

    def test_no_file_means_no_instance(self):
        self.assertIsNone(desktop.running_instance(self.path))

    def test_live_instance_is_found_and_file_is_private(self):
        desktop.record_instance(self.path, "http://127.0.0.1:1/?token=t", os.getpid())
        self.assertEqual(desktop.running_instance(self.path), "http://127.0.0.1:1/?token=t")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_stale_instance_is_ignored(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        desktop.record_instance(self.path, "http://x", proc.pid)
        self.assertIsNone(desktop.running_instance(self.path))

    def test_corrupt_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        Path(self.path).write_text("{")
        self.assertIsNone(desktop.running_instance(self.path))

    def test_forget_removes_only_own_record(self):
        desktop.record_instance(self.path, "http://x", os.getpid())
        desktop.forget_instance(self.path, os.getpid() + 1)
        self.assertTrue(os.path.exists(self.path))
        desktop.forget_instance(self.path, os.getpid())
        self.assertFalse(os.path.exists(self.path))


class AppModeTest(unittest.TestCase):
    """Run the real entry point with a stand-in for the browser window."""

    def launch(self, browser_script, home):
        browser = Path(home) / "browser.sh"
        browser.write_text("#!/bin/sh\n" + browser_script)
        browser.chmod(0o755)
        env = dict(os.environ, HOME=home, BENCH_WIZARD_BROWSER=str(browser))
        return subprocess.Popen([sys.executable, "-m", "bench_wizard", "--app"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_server_stops_when_the_window_closes(self):
        with tempfile.TemporaryDirectory() as home:
            proc = self.launch('echo "$@" > "$HOME/args"; sleep 1\n', home)
            try:
                proc.wait(timeout=15)
            finally:
                proc.kill()
            self.assertEqual(proc.returncode, 0)
            args = (Path(home) / "args").read_text()
            self.assertIn("--app=http://127.0.0.1:", args)
            self.assertIn("token=", args)
            self.assertFalse((Path(home) / ".local/state/bench-wizard/instance.json").exists())

    def test_second_launch_reopens_the_first_instance(self):
        with tempfile.TemporaryDirectory() as home:
            first = self.launch('echo "$@" >> "$HOME/args"; sleep 4\n', home)
            try:
                deadline = time.time() + 10
                while not (Path(home) / "args").exists() and time.time() < deadline:
                    time.sleep(0.1)
                second = self.launch('echo "$@" >> "$HOME/args"; sleep 4\n', home)
                second.wait(timeout=10)
                urls = [line.split()[0] for line in (Path(home) / "args").read_text().splitlines()]
                self.assertEqual(len(urls), 2)
                self.assertEqual(urls[0], urls[1])
            finally:
                first.kill()
                first.wait()


# Stands in for the browser: answers DevTools commands on fds 3 and 4 like Chrome does.
FAKE_BROWSER = r"""
import json, os, sys
state = json.loads(sys.argv[1])
buf = b""
while True:
    chunk = os.read(3, 65536)
    if not chunk:
        break
    buf += chunk
    while b"\0" in buf:
        raw, buf = buf.split(b"\0", 1)
        msg = json.loads(raw)
        os.write(4, json.dumps({"method": "Some.event"}).encode() + b"\0")  # events come between replies
        result = state.get(msg["method"], {})
        if msg["method"] == "Browser.setWindowBounds":
            result = {"seen": msg["params"]}
            state["Browser.getWindowForTarget"]["bounds"] = msg["params"]["bounds"]
        os.write(4, json.dumps({"id": msg["id"], "result": result}).encode() + b"\0")
"""


class DevToolsTest(unittest.TestCase):
    def start(self, state):
        devtools = desktop.DevTools()
        proc = subprocess.Popen([sys.executable, "-c", FAKE_BROWSER, json.dumps(state)],
                                close_fds=False, preexec_fn=devtools.child_setup)
        devtools.started()
        self.addCleanup(proc.wait)
        self.addCleanup(os.close, devtools._to_browser)
        return devtools, proc

    def state(self, url, window_state):
        return {"Target.getTargets": {"targetInfos": [
                    {"type": "page", "url": "chrome://newtab/", "targetId": "other"},
                    {"type": "page", "url": url, "targetId": "wiz"}]},
                "Browser.getWindowForTarget": {"windowId": 7, "bounds": {"windowState": window_state}}}

    def test_command_flag(self):
        self.assertIn("--remote-debugging-pipe", desktop.window_command("chrome", "http://x", "/s", devtools=True))
        self.assertNotIn("--remote-debugging-pipe", desktop.window_command("chrome", "http://x", "/s"))

    def test_minimised_wizard_window_is_restored(self):
        devtools, _ = self.start(self.state("http://127.0.0.1:47615/?token=t", "minimized"))
        self.assertTrue(desktop.show_page(devtools, "http://127.0.0.1:47615/"))
        window = devtools.call("Browser.getWindowForTarget", {"targetId": "wiz"})
        self.assertEqual(window["bounds"], {"windowState": "normal"})

    def test_visible_window_is_left_as_it_is(self):
        devtools, _ = self.start(self.state("http://127.0.0.1:47615/", "fullscreen"))
        self.assertTrue(desktop.show_page(devtools, "http://127.0.0.1:47615/"))
        window = devtools.call("Browser.getWindowForTarget", {"targetId": "wiz"})
        self.assertEqual(window["bounds"], {"windowState": "fullscreen"})

    def test_no_wizard_page_means_nothing_to_show(self):
        devtools, _ = self.start(self.state("http://127.0.0.1:9/", "normal"))
        self.assertFalse(desktop.show_page(devtools, "http://127.0.0.1:47615/"))

    def test_a_quit_browser_means_nothing_to_show(self):
        devtools, proc = self.start({})
        proc.kill()
        proc.wait()
        self.assertFalse(desktop.show_page(devtools, "http://127.0.0.1:47615/"))

    def test_activate(self):
        ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")
        self.assertTrue(desktop.activate(42, run=ok))

        def missing(argv, **kw):
            raise FileNotFoundError("osascript")
        self.assertFalse(desktop.activate(42, run=missing))


class LinuxTest(unittest.TestCase):
    def test_snap_browsers_are_recognised(self):
        self.assertEqual(desktop.snap_name("/snap/bin/chromium"), "chromium")
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp, "chromium-browser")  # Ubuntu's /usr/bin/chromium-browser
            shim.write_text("#!/bin/sh\nexec /snap/bin/chromium \"$@\"\n")
            self.assertEqual(desktop.snap_name(str(shim)), "chromium")
            binary = Path(tmp, "google-chrome")
            binary.write_bytes(b"\x7fELF.../snap/bin/chromium...")
            self.assertIsNone(desktop.snap_name(str(binary)))
        self.assertIsNone(desktop.snap_name("/nonexistent/chrome"))

    def test_snap_browser_gets_a_profile_it_can_use_and_no_pipe(self):
        command = desktop.window_command("/snap/bin/chromium", "http://x", "/state", devtools=True)
        self.assertIn(f"--user-data-dir={os.path.expanduser('~/snap/chromium/common/bench-wizard-window-profile')}",
                      command)
        self.assertNotIn("--remote-debugging-pipe", command)
        command = desktop.window_command("/usr/bin/google-chrome", "http://x", "/state", devtools=True)
        self.assertIn("--user-data-dir=/state/window-profile", command)
        self.assertIn("--remote-debugging-pipe", command)

    def test_reveal_without_a_file_manager(self):
        real = subprocess.Popen

        def missing(*a, **kw):
            raise FileNotFoundError("xdg-open")
        subprocess.Popen = missing
        try:
            self.assertFalse(desktop.reveal("/tmp", system="linux"))
        finally:
            subprocess.Popen = real
