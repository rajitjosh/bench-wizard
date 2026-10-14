import os
import queue
import tempfile
import time
import unittest
from pathlib import Path

from bench_wizard.planner import Step
from bench_wizard.runner import Runner


def sh(id, script, cwd=None, **extra):
    return Step(id, id, ["sh", "-c", script], cwd or os.getcwd(), **extra)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.logs = self.dir / "logs"

    def runner(self, steps, services=()):
        r = Runner(steps, str(self.logs), dict(os.environ), services=services)
        self.addCleanup(r.cancel)
        return r

    def wait(self, r, *states, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if r.snapshot()["state"] in states:
                return r.snapshot()
            time.sleep(0.02)
        self.fail(f"runner stuck in {r.snapshot()['state']}")

    def states(self, r):
        return [s["state"] for s in r.snapshot()["steps"]]

    def test_success_runs_every_step_and_writes_logs(self):
        r = self.runner([sh("a", "echo one"), sh("b", "echo two; echo err >&2")])
        r.start()
        snap = self.wait(r, "done")
        self.assertEqual(self.states(r), ["done", "done"])
        self.assertEqual((self.logs / "a.log").read_text().strip().splitlines()[-1], "one")
        self.assertIn("err", (self.logs / "b.log").read_text())
        self.assertIsNotNone(snap["steps"][0]["duration"])

    def test_failure_stops_the_run(self):
        r = self.runner([sh("a", "exit 3"), sh("b", "echo never")])
        r.start()
        self.wait(r, "failed")
        self.assertEqual(self.states(r), ["failed", "waiting"])
        self.assertFalse((self.logs / "b.log").exists())

    def test_retry_continues_from_failed_step(self):
        flag = self.dir / "flag"
        counter = self.dir / "count"
        r = self.runner([
            sh("a", f"echo x >> {counter}"),
            sh("b", f"test -f {flag}"),
            sh("c", "echo end"),
        ])
        r.start()
        self.wait(r, "failed")
        flag.write_text("")
        r.retry(cleanup=False)
        self.wait(r, "done")
        self.assertEqual(self.states(r), ["done", "done", "done"])
        self.assertEqual(counter.read_text().count("x"), 1)

    def test_optional_cleanup_runs_only_when_asked(self):
        mark = self.dir / "cleaned"
        step = sh("a", "exit 1", cleanup=["sh", "-c", f"echo c >> {mark}"])
        r = self.runner([step])
        r.start()
        self.wait(r, "failed")
        r.retry(cleanup=False)
        self.wait(r, "failed")
        self.assertFalse(mark.exists())
        r.retry(cleanup=True)
        self.wait(r, "failed")
        self.assertEqual(mark.read_text().count("c"), 1)

    def test_required_cleanup_always_runs_on_retry(self):
        mark = self.dir / "cleaned"
        step = sh("a", "exit 1", cleanup=["sh", "-c", f"echo c >> {mark}"], cleanup_required=True)
        r = self.runner([step])
        r.start()
        self.wait(r, "failed")
        r.retry(cleanup=False)
        self.wait(r, "failed")
        self.assertTrue(mark.exists())

    def test_retry_argv_used_on_retry(self):
        out = self.dir / "out"
        step = sh("a", f"echo first >> {out}; exit 1", retry_argv=["sh", "-c", f"echo second >> {out}"])
        r = self.runner([step])
        r.start()
        self.wait(r, "failed")
        r.retry(cleanup=False)
        self.wait(r, "done")
        self.assertEqual(out.read_text().split(), ["first", "second"])

    def test_missing_command_fails_the_step_with_a_message(self):
        r = self.runner([Step("a", "a", ["no-such-command-xyz"], os.getcwd())])
        r.start()
        self.wait(r, "failed")
        self.assertIn("no-such-command-xyz", (self.logs / "a.log").read_text())

    def test_missing_cwd_fails_the_step_with_a_message(self):
        r = self.runner([sh("a", "echo hi", cwd=str(self.dir / "nope"))])
        r.start()
        self.wait(r, "failed")
        self.assertIn("nope", (self.logs / "a.log").read_text())

    def test_stdin_is_closed_so_prompts_cannot_hang(self):
        r = self.runner([sh("a", "read answer && echo got")])
        r.start()
        self.wait(r, "failed", timeout=5)

    def test_cancel_kills_child_processes(self):
        pidfile = self.dir / "pid"
        r = self.runner([sh("a", f"sh -c 'echo $$ > {pidfile}; sleep 60' & wait"), sh("b", "echo never")])
        r.start()
        deadline = time.time() + 5
        while not (pidfile.exists() and pidfile.read_text().strip()) and time.time() < deadline:
            time.sleep(0.02)
        pid = int(pidfile.read_text())
        r.cancel()
        self.wait(r, "cancelled")
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.states(r)[1], "waiting")

    def test_events_arrive_in_order(self):
        r = self.runner([sh("a", "echo hello")])
        q = r.subscribe()
        r.start()
        self.wait(r, "done")
        events = []
        while True:
            try:
                events.append(q.get(timeout=0.5))
            except queue.Empty:
                break
        kinds = [(e["type"], e.get("state") or e.get("line")) for e in events]
        self.assertEqual(kinds[0], ("run", "running"))
        self.assertIn(("step", "running"), kinds)
        self.assertIn(("log", "hello"), kinds)
        self.assertLess(kinds.index(("step", "running")), kinds.index(("log", "hello")))
        self.assertLess(kinds.index(("log", "hello")), kinds.index(("step", "done")))
        self.assertEqual(kinds[-1], ("run", "done"))

    def test_snapshot_command_masks_secrets_and_log_keeps_output(self):
        step = Step("a", "a", ["sh", "-c", "echo admin panel", "sh", "s3cret"], os.getcwd(), secret_args=(4,))
        r = self.runner([step])
        r.start()
        snap = self.wait(r, "done")
        self.assertNotIn("s3cret", snap["steps"][0]["command"])
        self.assertIn("••••", snap["steps"][0]["command"])
        log = (self.logs / "a.log").read_text()
        self.assertNotIn("s3cret", log)
        self.assertIn("admin panel", log)

    def test_credentials_in_a_url_are_scrubbed_from_log_and_events(self):
        r = self.runner([sh("a", "echo fatal: https://bob:ghp_secret123@github.com/x/y.git denied")])
        q = r.subscribe()
        r.start()
        self.wait(r, "done")
        log = (self.logs / "a.log").read_text()
        self.assertIn("fatal: https://github.com/x/y.git denied", log)
        events = []
        while not q.empty():
            events.append(str(q.get()))
        self.assertNotIn("ghp_secret123", log + "".join(events))

    def test_start_twice_is_refused(self):
        r = self.runner([sh("a", "sleep 0.3")])
        r.start()
        with self.assertRaises(RuntimeError):
            r.start()
        self.wait(r, "done")

    def test_retry_only_after_failure(self):
        r = self.runner([sh("a", "true")])
        r.start()
        self.wait(r, "done")
        with self.assertRaises(RuntimeError):
            r.retry(cleanup=False)


class ServiceTest(RunnerTest):
    """Background processes (the bench's Redis) that live only while site steps run."""

    def service(self, before="b"):
        self.alive = self.dir / "alive"
        script = f"echo $$ >> {self.dir / 'pids'}; touch {self.alive}; trap 'rm -f {self.alive}; exit 0' TERM; while :; do sleep 0.1; done"
        return [{"argv": ["sh", "-c", script], "cwd": str(self.dir), "before": before}]

    def gone(self):
        deadline = time.time() + 5
        while self.alive.exists() and time.time() < deadline:
            time.sleep(0.05)
        return not self.alive.exists()

    def test_service_runs_from_its_step_until_the_run_ends(self):
        seen = self.dir / "seen"
        r = self.runner([
            sh("a", f"test ! -e {self.dir / 'alive'}"),
            sh("b", f"test -e {self.dir / 'alive'} && echo yes > {seen}"),
            sh("c", f"test -e {self.dir / 'alive'}"),
        ], self.service())
        r.start()
        self.wait(r, "done")
        self.assertEqual(self.states(r), ["done", "done", "done"])
        self.assertTrue(seen.exists())
        self.assertTrue(self.gone())

    def test_service_is_stopped_when_a_step_fails_and_restarted_on_retry(self):
        flag = self.dir / "flag"
        r = self.runner([sh("a", "true"), sh("b", f"test -e {self.dir / 'alive'} && test -f {flag}")],
                        self.service())
        r.start()
        self.wait(r, "failed")
        self.assertTrue(self.gone())
        flag.write_text("")
        r.retry(cleanup=False)
        self.wait(r, "done")
        self.assertEqual(len((self.dir / "pids").read_text().split()), 2)
        self.assertTrue(self.gone())

    def test_service_is_stopped_on_cancel(self):
        r = self.runner([sh("a", "true"), sh("b", "sleep 30")], self.service())
        r.start()
        deadline = time.time() + 5
        while not self.alive.exists() and time.time() < deadline:
            time.sleep(0.02)
        r.cancel()
        self.wait(r, "cancelled")
        self.assertTrue(self.gone())

    def test_service_that_exits_at_once_does_not_fail_the_run(self):
        r = self.runner([sh("a", "true"), sh("b", "true")],
                        [{"argv": ["sh", "-c", "exit 1"], "cwd": str(self.dir), "before": "b"}])
        r.start()
        self.wait(r, "done")

    def test_service_that_cannot_start_does_not_fail_the_run(self):
        r = self.runner([sh("a", "true")],
                        [{"argv": ["no-such-redis-xyz"], "cwd": str(self.dir), "before": "a"}])
        r.start()
        self.wait(r, "done")
