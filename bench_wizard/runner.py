"""Run planned steps one at a time, keeping logs and publishing progress."""
import os
import queue
import signal
import subprocess
import threading
import time

from .gitauth import scrub


class Runner:
    def __init__(self, steps, log_dir, env, services=()):
        self.steps = steps
        self.services = list(services)  # {"argv", "cwd", "before": step id}
        self._service_procs = []
        self.log_dir = log_dir
        self.env = dict(env, GIT_TERMINAL_PROMPT="0")
        self.state = "idle"  # idle, running, failed, done, cancelled
        self.failed_index = None
        self._states = {s.id: "waiting" for s in steps}
        self._durations = {s.id: None for s in steps}
        self._lock = threading.Lock()
        self._subscribers = []
        self._proc = None
        self._cancelled = False
        self._thread = None
        os.makedirs(log_dir, exist_ok=True)

    # -- subscriptions -------------------------------------------------
    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, event):
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            q.put(event)

    # -- state ---------------------------------------------------------
    def snapshot(self):
        return {
            "state": self.state,
            "failed": self.steps[self.failed_index].id if self.failed_index is not None else None,
            "steps": [{
                "id": s.id, "title": s.title, "command": s.display(),
                "state": self._states[s.id], "duration": self._durations[s.id],
                "cleanup": s.display(s.cleanup, s.cleanup_secret_args) if s.cleanup else None,
                "cleanup_required": s.cleanup_required,
            } for s in self.steps],
        }

    def log_path(self, step_id):
        return os.path.join(self.log_dir, f"{step_id}.log")

    def read_log(self, step_id):
        try:
            with open(self.log_path(step_id), errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def _set_run(self, state):
        self.state = state
        self._publish({"type": "run", "state": state})

    def _set_step(self, step, state, duration=None):
        self._states[step.id] = state
        self._durations[step.id] = duration
        self._publish({"type": "step", "id": step.id, "state": state, "duration": duration})

    def _log(self, step, line):
        line = scrub(line)  # a credential inside a URL must not reach the log
        with open(self.log_path(step.id), "a") as fh:
            fh.write(line + "\n")
        self._publish({"type": "log", "id": step.id, "line": line})

    # -- control -------------------------------------------------------
    def start(self):
        if self.state != "idle":
            raise RuntimeError("run already started")
        self._launch(0, False, False)

    def retry(self, cleanup):
        if self.state != "failed":
            raise RuntimeError("nothing to retry")
        self._launch(self.failed_index, True, cleanup)

    def _launch(self, index, retrying, cleanup):
        self.failed_index = None
        self._cancelled = False
        self._set_run("running")
        self._thread = threading.Thread(target=self._run, args=(index, retrying, cleanup), daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._kill(proc, signal.SIGTERM)
            threading.Timer(5, lambda: proc.poll() is None and self._kill(proc, signal.SIGKILL)).start()

    @staticmethod
    def _kill(proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    # -- execution -----------------------------------------------------
    def _start_services(self, upto):
        """Start every service whose step is at or before index `upto` and is not running yet."""
        ids = [s.id for s in self.steps]
        started = False
        for service in self.services:
            if service.get("proc") or service["before"] not in ids or ids.index(service["before"]) > upto:
                continue
            try:
                service["proc"] = subprocess.Popen(
                    service["argv"], cwd=service["cwd"], env=self.env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                started = True
            except OSError:
                service["proc"] = None  # e.g. already provided by a running bench
        if started:
            time.sleep(1)

    def _stop_services(self):
        for service in self.services:
            proc = service.pop("proc", None)
            if proc is not None and proc.poll() is None:
                self._kill(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._kill(proc, signal.SIGKILL)

    def _run(self, index, retrying, cleanup):
        try:
            self._run_steps(index, retrying, cleanup)
        finally:
            self._stop_services()

    def _finish(self, state):
        # Services must be down before anyone is told the run has ended.
        self._stop_services()
        self._set_run(state)

    def _run_steps(self, index, retrying, cleanup):
        for i in range(index, len(self.steps)):
            step = self.steps[i]
            self._start_services(i)
            argv = step.argv
            started = time.time()
            self._set_step(step, "running")
            ok = True
            if retrying and i == index:
                if step.cleanup and (cleanup or step.cleanup_required):
                    self._log(step, "$ " + step.display(step.cleanup, step.cleanup_secret_args))
                    ok = self._exec(step, step.cleanup, step.cwd)
                argv = step.retry_argv or step.argv
            if ok and not self._cancelled:
                shown = step.display(argv, step.secret_args) if argv is step.argv else step.display(argv, ())
                self._log(step, "$ " + shown)
                ok = self._exec(step, argv, step.cwd)
            duration = round(time.time() - started, 1)
            if self._cancelled:
                self._set_step(step, "failed", duration)
                self.failed_index = i
                self._finish("cancelled")
                return
            if not ok:
                self._set_step(step, "failed", duration)
                self.failed_index = i
                self._finish("failed")
                return
            self._set_step(step, "done", duration)
        self._finish("done")

    def _exec(self, step, argv, cwd):
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors="replace", bufsize=1, start_new_session=True)
        except OSError as exc:
            self._log(step, f"could not start {argv[0]}: {exc}")
            return False
        self._proc = proc
        with proc.stdout:
            for line in proc.stdout:
                self._log(step, line.rstrip("\n"))
        code = proc.wait()
        self._proc = None
        if code != 0:
            self._log(step, f"exit code {code}")
        return code == 0
