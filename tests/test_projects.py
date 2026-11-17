import json
import os
import socket
import stat
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

from bench_wizard import projects

Disk = namedtuple("Disk", "total used free")
GB = 1024 ** 3


def make_bench(folder, ports=None, sites=("a.localhost",), version="15.40.0"):
    folder = Path(folder)
    (folder / "apps" / "frappe" / "frappe").mkdir(parents=True)
    (folder / "apps" / "frappe" / "frappe" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    for site in sites:
        (folder / "sites" / site).mkdir(parents=True)
        (folder / "sites" / site / "site_config.json").write_text("{}")
    (folder / "sites").mkdir(exist_ok=True)
    if ports is not None:
        (folder / "sites" / "common_site_config.json").write_text(json.dumps(ports))
    return str(folder)


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def test_discover_finds_benches_three_deep_and_skips_hidden_and_inside_benches(self):
        one = make_bench(self.home / "git" / "proj" / "frappe-bench")
        two = make_bench(self.home / "bench2")
        make_bench(self.home / ".cache" / "x")
        make_bench(self.home / "a" / "b" / "c" / "too-deep")
        make_bench(Path(two) / "apps" / "inner")  # inside a bench: never searched
        self.assertEqual(sorted(projects.discover(str(self.home))), sorted([one, two]))

    def test_bench_info_reads_version_sites_and_ports(self):
        folder = make_bench(self.home / "b", {"webserver_port": 8001, "redis_cache": "redis://127.0.0.1:13001",
                                             "redis_queue": "redis://localhost:11001/0"}, sites=("y.local", "x.local"))
        info = projects.bench_info(folder)
        self.assertEqual((info["version"], info["sites"]), ("version-15", ["x.local", "y.local"]))
        self.assertEqual(info["ports"], {"webserver_port": 8001, "socketio_port": 9000, "file_watcher_port": 6787,
                                         "redis_cache": 13001, "redis_queue": 11001})

    def test_clashes_list_shared_ports_only(self):
        base, other = projects.slot(0), projects.slot(1)
        found = projects.clashes({"a": base, "b": dict(base, webserver_port=8005), "c": other})
        self.assertEqual(found["a"], sorted(set(base.values()) - {8000}))
        self.assertEqual(found["c"], [])

    def test_allocate_moves_later_benches_to_unused_free_slots(self):
        base = projects.slot(0)
        benches = [("run", base), ("b", base), ("c", projects.slot(1)), ("d", base)]
        busy = {8002}  # something else listens on slot 2's web port
        moves = projects.allocate(benches, is_free=lambda port: port not in busy)
        self.assertEqual(moves, {"b": projects.slot(3), "d": projects.slot(4)})

    def test_allocate_leaves_benches_without_clashes(self):
        self.assertEqual(projects.allocate([("a", projects.slot(0)), ("b", projects.slot(1))]), {})

    def test_port_free(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            self.assertFalse(projects.port_free(sock.getsockname()[1]))

    def test_resources_warn_on_low_memory_and_disk(self):
        low = projects.resources("/", mem=(16 * GB, 1 * GB), disk=Disk(100 * GB, 95 * GB, 5 * GB))
        self.assertEqual(len(low["warnings"]), 2)
        self.assertIn("memory", low["warnings"][0])
        ok = projects.resources("/", mem=(16 * GB, 8 * GB), disk=Disk(100 * GB, 50 * GB, 50 * GB))
        self.assertEqual((ok["warnings"], ok["memory"]["available"]), ([], 8 * GB))

    @unittest.skipUnless(os.path.isdir("/proc/self"), "Linux only")
    def test_usage_counts_bench_processes_by_working_folder(self):
        import subprocess
        import sys
        folder = make_bench(self.home / "b")
        # a process whose command line mentions frappe, running inside the bench
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)", "frappe.utils.bench_helper"],
                                cwd=folder)
        self.addCleanup(proc.kill)
        # an editor or agent open in the bench, with the bench's path in its arguments, is not the bench
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)", folder + "/apps/frappe"], cwd=folder)
        self.addCleanup(other.kill)
        used = projects.usage([folder, str(self.home / "other")])
        self.assertEqual(list(used), [folder])
        self.assertEqual(used[folder]["procs"], 1)

    def test_bench_process_pattern(self):
        match = lambda *argv: bool(projects.BENCH_PROCS.search(b"\0".join(a.encode() for a in argv)))
        for argv in (["/usr/bin/python3", "/home/u/.local/bin/honcho", "start"], ["redis-server 127.0.0.1:13000"],
                     ["/bin/sh", "-c", "bench serve  --port 8000"], ["/usr/bin/python3", "/home/u/.local/bin/bench", "start"],
                     ["env/bin/python", "-m", "frappe.utils.bench_helper", "frappe", "worker"],
                     ["/usr/bin/node", "apps/frappe/socketio.js"]):
            self.assertTrue(match(*argv), argv)
        for argv in (["/usr/share/cursor/cursor", "--folder", "/home/u/git/x/frappe-bench/apps/frappe"],
                     ["claude"], ["bash"], ["vim", "frappe-bench/sites/common_site_config.json"]):
            self.assertFalse(match(*argv), argv)


@unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
class StartStopTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.folder = make_bench(self.tmp / "bench")
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def script(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_a_bench_that_fails_at_once_gives_its_last_lines(self):
        self.script("bench", "#!/bin/sh\necho 'redis: Address already in use'\nexit 1\n")
        self.assertIn("Address already in use", projects.start(self.folder, self.env, wait=5))

    def test_start_then_stop(self):
        # A stand-in honcho: `bench start` runs it, and it runs until it is stopped.
        self.script("honcho", "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
        self.script("bench", f"#!/bin/sh\nexec {self.bin}/honcho start\n")
        self.assertIsNone(projects.start(self.folder, self.env, wait=1))
        self.addCleanup(projects.stop, self.folder, wait=5)
        self.assertIn(self.folder, projects.usage([self.folder]))
        self.assertEqual(projects.stop(self.folder, wait=5), 1)
        self.assertNotIn(self.folder, projects.usage([self.folder]))
        self.assertEqual(projects.stop(self.folder, wait=1), 0)
