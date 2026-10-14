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
            path = desktop.install(str(ROOT), applications_dir=tmp)
            self.assertEqual(path, os.path.join(tmp, "bench-wizard.desktop"))
            self.assertIn("--app", Path(path).read_text())
        self.assertTrue((ROOT / "bench_wizard/web/icon.svg").exists())


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
