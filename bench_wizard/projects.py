"""Every bench on this machine: where it is, its sites and ports, what it holds in memory, and what is left."""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

# bench's own defaults (bench/config/common_site_config.py); a bench in slot n uses each plus n.
BASE_PORTS = {"webserver_port": 8000, "socketio_port": 9000, "file_watcher_port": 6787,
              "redis_cache": 13000, "redis_queue": 11000}
REDIS_KEYS = ("redis_cache", "redis_queue")
# Folders a bench never sits in; skipping them keeps the scan of the home folder quick.
# Pictures, Movies and Music are also macOS privacy-protected: reading them pops up a permission prompt.
SKIP = {"node_modules", "env", "apps", "sites", "logs", "__pycache__", "Library", "snap", "site-packages",
        "Pictures", "Movies", "Music"}
# The processes `bench start` runs, matched on their program and arguments, not on any path: an editor, shell
# or AI agent working in the bench folder has "frappe-bench" in its arguments but is not the bench running.
BENCH_PROCS = re.compile(rb"(?:^|[\0/])(?:honcho|redis-server|gunicorn)(?:\0|$| )|frappe\.utils\.bench_helper"
                         rb"|(?:^|[\0/ ])bench[\0 ](?:start|serve|worker|schedule|watch)\b|apps/frappe/socketio\.js")
HONCHO = re.compile(rb"(?:^|[\0/])honcho(?:\0|$| )")
START_LOG = os.path.join("logs", "bench-start.log")
LOW_MEMORY = 2 * 1024 ** 3
LOW_DISK = 10 * 1024 ** 3


def is_bench(folder):
    return (os.path.isfile(os.path.join(folder, "apps", "frappe", "frappe", "__init__.py"))
            and os.path.isdir(os.path.join(folder, "sites")))


def discover(root, depth=3):
    """Bench folders at most `depth` folders below `root`; nothing inside a bench is searched."""
    found = []

    def walk(folder, left):
        if is_bench(folder):
            found.append(folder)
            return
        if left == 0:
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in SKIP:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(entry.path, left - 1)
            except OSError:
                pass

    walk(os.path.abspath(os.path.expanduser(root)), depth)
    return found


def _port(value):
    if isinstance(value, int):
        return value
    match = re.search(r":(\d+)/?\d*$", str(value or ""))  # redis://127.0.0.1:13000 or redis://h:13000/0
    return int(match.group(1)) if match else None


def read_ports(folder):
    """The ports in a bench's sites/common_site_config.json; bench's defaults for any it does not set."""
    try:
        with open(os.path.join(folder, "sites", "common_site_config.json")) as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        config = {}
    config = config if isinstance(config, dict) else {}
    return {key: _port(config.get(key)) or base for key, base in BASE_PORTS.items()}


def bench_info(folder):
    """What the Projects screen shows of a bench, read from its files only (no bench command runs)."""
    try:
        with open(os.path.join(folder, "apps", "frappe", "frappe", "__init__.py")) as fh:
            match = re.search(r"""__version__\s*=\s*["'](\d+)\.""", fh.read())
    except OSError:
        match = None
    sites_dir = os.path.join(folder, "sites")
    try:
        sites = sorted(d for d in os.listdir(sites_dir) if os.path.isfile(os.path.join(sites_dir, d, "site_config.json")))
    except OSError:
        sites = []
    return {"folder": folder, "version": f"version-{match.group(1)}" if match else "", "sites": sites,
            "ports": read_ports(folder)}


def clashes(benches):
    """{folder: the ports it shares with another bench} for {folder: ports}."""
    users = {}
    for folder, ports in benches.items():
        for port in set(ports.values()):
            users.setdefault(port, set()).add(folder)
    return {folder: sorted(p for p in set(ports.values()) if len(users[p]) > 1) for folder, ports in benches.items()}


def slot(n):
    return {key: base + n for key, base in BASE_PORTS.items()}


def port_free(port):
    """Nothing on this machine listens on `port` (127.0.0.1)."""
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    # macOS can let that bind through while a dual-stack ([::]) listener, such as Node's, holds the port
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def free_slot(used, is_free=port_free):
    """The first slot none of whose ports is in `used` or listened on by anything on this machine."""
    for n in range(1000):
        ports = slot(n)
        if not set(ports.values()) & used and all(is_free(p) for p in ports.values()):
            return ports
    raise RuntimeError("no free port slot below 1000")


def allocate(benches, is_free=port_free):
    """New ports for each bench that shares a port with one before it in `benches`, a list of (folder, ports).

    Earlier benches keep their ports, so list running ones first. A moved bench gets the first slot n
    (every port at bench's default + n) that no bench uses and nothing on the machine listens on.
    """
    used = {port for _, ports in benches for port in ports.values()}
    kept, moves = set(), {}
    for folder, ports in benches:
        mine = set(ports.values())
        if not mine & kept:
            kept |= mine
            continue
        new = moves[folder] = free_slot(used, is_free)
        used |= set(new.values())
        kept |= set(new.values())
    return moves


def _procs():
    """(pid, cwd, cmdline, rss) of every bench process on this machine; None where this cannot be told."""
    return _procs_linux() if os.path.isdir("/proc/self") else _procs_ps()


def _procs_linux():
    page = os.sysconf("SC_PAGE_SIZE")
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read()
            if not BENCH_PROCS.search(cmd):
                continue
            with open(f"/proc/{pid}/statm") as fh:
                found.append((int(pid), cwd, cmd, int(fh.read().split()[1]) * page))
        except (OSError, ValueError, IndexError):
            continue
    return found


def _procs_ps():
    """The same from `ps` and `lsof`, for macOS, which has no /proc. Arguments come space-separated here."""
    try:
        listing = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,rss=,command="], capture_output=True, text=True,
                                 timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    bench = {}
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and BENCH_PROCS.search(parts[2].encode()):
            bench[int(parts[0])] = (parts[2].encode(), int(parts[1]) * 1024)  # ps gives rss in KB
    if not bench:
        return []
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"  # /usr/sbin is often not on PATH
    try:  # -w: no warnings; lsof exits 1 when a process is gone by now, so only its output counts
        out = subprocess.run([lsof, "-w", "-a", "-d", "cwd", "-Fn", "-p", ",".join(map(str, bench))],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    cwds, pid = {}, None
    for line in out.splitlines():
        if line[:1] == "p" and line[1:].isdigit():
            pid = int(line[1:])
        elif line[:1] == "n" and pid in bench:
            cwds[pid] = line[1:]
    return [(pid, cwds[pid], *bench[pid]) for pid in bench if pid in cwds]


def _inside(path, folder):
    return path == folder or path.startswith(folder + os.sep)


def usage(folders):
    """{folder: {"rss": bytes, "procs": n}} for the benches that have processes running in them now; None
    where this system cannot tell."""
    procs = _procs()
    if procs is None:
        return None
    found = {}
    for _, cwd, _, rss in procs:
        folder = next((f for f in folders if _inside(cwd, f)), None)
        if folder:
            entry = found.setdefault(folder, {"rss": 0, "procs": 0})
            entry["rss"] += rss
            entry["procs"] += 1
    return found


def start(folder, env, wait=3):
    """Run `bench start` in `folder` in a session of its own, so it keeps running after the wizard closes.

    Its output goes to logs/bench-start.log. Returns None if it is still running after `wait` seconds,
    else the last lines it printed: honcho stops everything at once when one process fails, e.g. on a
    port in use.
    """
    path = os.path.join(folder, START_LOG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as log:
        proc = subprocess.Popen(["bench", "start"], cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
    try:
        proc.wait(timeout=wait)
        with open(path, errors="replace") as fh:
            return "".join(fh.readlines()[-12:]).strip() or f"bench start exited with code {proc.returncode}"
    except subprocess.TimeoutExpired:
        return None


def stop(folder, wait=15):
    """Stop the bench running in `folder` as Ctrl+C would: honcho stops every process it started.

    Returns how many processes were signalled (0: nothing was running), or None where this cannot be told.
    Raises PermissionError for a bench run by another user.
    """
    procs = _procs()
    if procs is None:
        return None
    mine = [p for p in procs if _inside(p[1], folder)]
    # honcho's per-process helpers share its name but ignore SIGTERM; its manager stops them all.
    targets = [p for p in mine if HONCHO.search(p[2])] or mine  # no honcho: `bench serve` or a lone worker
    for pid, *_ in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + wait
    while targets and time.monotonic() < deadline and any(_inside(p[1], folder) for p in _procs() or ()):
        time.sleep(0.3)
    return len(targets)


def memory():
    """(total, available) bytes of RAM, or None when this system does not say."""
    try:
        with open("/proc/meminfo") as fh:
            info = dict(line.split(":", 1) for line in fh)
        return int(info["MemTotal"].split()[0]) * 1024, int(info["MemAvailable"].split()[0]) * 1024
    except (OSError, KeyError, ValueError):
        pass
    if sys.platform == "darwin":
        try:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                       timeout=5).stdout)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+)", vm).group(1))
            pages = sum(int(n) for n in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", vm))
            return total, pages * page
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            pass
    return None


def _gb(n):
    return f"{n / 1024 ** 3:.1f} GB"


def resources(path, mem=None, disk=None):
    """RAM and disk left, with a warning for each that is running low."""
    mem = mem if mem is not None else memory()
    disk = disk or shutil.disk_usage(path)
    warnings = []
    if mem and mem[1] < LOW_MEMORY:
        warnings.append(f"Only {_gb(mem[1])} of memory is free. Stop benches you are not using before you "
                        "start another, or the machine will slow down and builds may be killed.")
    if disk.free < LOW_DISK:
        warnings.append(f"Only {_gb(disk.free)} of disk space is free. A new bench needs about 3 GB and "
                        "restored sites need more; free up space or remove benches you no longer use.")
    return {"memory": {"total": mem[0], "available": mem[1]} if mem else None,
            "disk": {"path": path, "total": disk.total, "free": disk.free}, "warnings": warnings}
