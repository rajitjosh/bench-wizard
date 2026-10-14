"""Detect and install what a Frappe bench needs on an Ubuntu machine."""
import glob
import os
import re
import subprocess

BUILD_PACKAGES = ["git", "libffi-dev", "libssl-dev", "build-essential", "libmysqlclient-dev", "curl",
                  "software-properties-common"]
CNF_PATH = "/etc/mysql/mariadb.conf.d/99-frappe.cnf"
CNF = """[mysqld]
character-set-client-handshake = FALSE
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
"""
NVM_INSTALL = "https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh"
APT = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y"]
SHIMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shims")


def _home(home):
    return home or os.path.expanduser("~")


def _version_key(text):
    return tuple(int(n) for n in re.findall(r"\d+", text))


def node_bin(node, home=None):
    """The bin directory of the newest nvm-installed Node with this major version."""
    found = glob.glob(os.path.join(_home(home), ".nvm/versions/node", f"v{node}.*", "bin"))
    found = [d for d in found if os.path.exists(os.path.join(d, "node"))]
    return max(found, key=lambda d: _version_key(d.split("/")[-2])) if found else None


def build_env(node, home=None, base=None):
    """Environment for bench commands: shims, the profile's Node and ~/.local/bin first on PATH."""
    env = dict(os.environ if base is None else base)
    first = [d for d in (SHIMS_DIR, node_bin(node, home), os.path.join(_home(home), ".local/bin")) if d]
    env["PATH"] = ":".join(first + [env.get("PATH", "/usr/bin:/bin")])
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_cmd(argv, env=None, input=None, timeout=60):
    env = dict(env or os.environ)
    env["PATH"] = env.get("PATH", "") + ":/usr/sbin:/sbin"
    try:
        result = subprocess.run(argv, env=env, input=input, stdin=None if input is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                timeout=timeout)
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]} timed out after {timeout}s"
    except OSError as exc:
        return 127, str(exc)


def _first_version(text):
    match = re.search(r"\d+\.\d+(?:\.\d+)?", text or "")
    return match.group(0) if match else ""


def _row(id, title, note, required, status, found=""):
    return {"id": id, "title": title, "note": note, "required": required, "status": status, "found": found}


def detect_all(profile, run=run_cmd, home=None):
    env = build_env(profile["node"], home)
    rows = []

    rc, out = run(["dpkg-query", "-W", "-f=${Package} ${Status}\n", *BUILD_PACKAGES], env)
    installed = {line.split()[0] for line in out.splitlines() if line.endswith("install ok installed")}
    missing = [p for p in BUILD_PACKAGES if p not in installed]
    rows.append(_row("build", "Git and build tools", ", ".join(BUILD_PACKAGES), "installed",
                     "missing" if missing else "ok",
                     ("missing: " + ", ".join(missing)) if missing and installed else ("installed" if installed else "")))

    python = f"python{profile['python']}"
    probe = ("import sys, venv, ensurepip, sysconfig, os; "
             "h = os.path.exists(os.path.join(sysconfig.get_paths()['include'], 'Python.h')); "
             "print('%d.%d.%d' % sys.version_info[:3], 'headers' if h else 'no-headers')")
    rc, out = run([python, "-c", probe], env)
    if rc == 0 and out.split()[-1:] == ["headers"]:
        rows.append(_row("python", "Python", "with venv and development headers", profile["python"], "ok",
                         out.split()[0]))
    elif rc == 0:
        # Apps with compiled dependencies (mysqlclient, for one) need Python.h to install.
        rows.append(_row("python", "Python", "with venv and development headers", profile["python"],
                         "needs_fix", f"{out.split()[0]}, development headers missing"))
    else:
        rc, out = run([python, "--version"], env)
        if rc == 0:
            rows.append(_row("python", "Python", "", profile["python"], "needs_fix",
                             f"{_first_version(out)}, venv missing"))
        else:
            rows.append(_row("python", "Python", "", profile["python"], "missing"))

    rc, out = run(["mariadbd", "--version"], env)
    match = re.search(r"(\d+\.\d+\.\d+)-MariaDB", out) if rc == 0 else None
    if not match:
        rows.append(_row("mariadb", "MariaDB", "", "10.6+", "missing"))
    else:
        ok = _version_key(match.group(1)) >= (10, 6)
        rows.append(_row("mariadb", "MariaDB", "", "10.6+", "ok" if ok else "needs_fix", match.group(1)))

    note = "utf8mb4 / utf8mb4_unicode_ci in 99-frappe.cnf"
    rc, out = run(["my_print_defaults", "mysqld", "server", "mariadb", "mariadbd"], env)
    if rc != 0:
        rows.append(_row("charset", "MariaDB charset", note, "utf8mb4", "missing"))
    else:
        options = {}
        for line in out.splitlines():
            key, _, value = line.lstrip("-").partition("=")
            options[key.replace("_", "-")] = value.strip()
        charset = options.get("character-set-server", "")
        collation = options.get("collation-server", "")
        ok = charset == "utf8mb4" and collation == "utf8mb4_unicode_ci"
        found = " / ".join(v for v in (charset, collation) if v) or "not set"
        rows.append(_row("charset", "MariaDB charset", note, "utf8mb4", "ok" if ok else "needs_fix", found))

    rc, out = run(["redis-server", "--version"], env)
    match = re.search(r"v=(\S+)", out) if rc == 0 else None
    rows.append(_row("redis", "Redis", "", "any", "ok" if match else "missing", match.group(1) if match else ""))

    bin_dir = node_bin(profile["node"], home)
    note = "Only ~/.nvm/versions/node is checked"
    rows.append(_row("node", "Node via nvm", note, profile["node"], "ok" if bin_dir else "missing",
                     bin_dir.split("/")[-2].lstrip("v") if bin_dir else ""))

    rc, out = run([os.path.join(bin_dir, "yarn"), "--version"], env) if bin_dir else (127, "")
    version = _first_version(out) if rc == 0 else ""
    status = "missing" if not version else ("ok" if version.startswith("1.") else "needs_fix")
    rows.append(_row("yarn", "Yarn", f"For Node {profile['node']}", "1.x", status, version))

    rc, out = run(["wkhtmltopdf", "--version"], env)
    version = _first_version(out) if rc == 0 else ""
    rows.append(_row("wkhtmltopdf", "wkhtmltopdf", "", "any", "ok" if version else "missing", version))

    rc, out = run(["bench", "--version"], env)
    version = _first_version(out) if rc == 0 else ""
    rows.append(_row("bench", "Bench CLI", "", "any", "ok" if version else "missing", version))
    return rows


def ubuntu_version():
    try:
        with open("/etc/os-release") as fh:
            match = re.search(r'^VERSION_ID="?([\d.]+)', fh.read(), re.M)
            return match.group(1) if match else ""
    except OSError:
        return ""


def _cmd(argv, sudo=False, input=None):
    return {"argv": argv, "sudo": sudo, "input": input}


def install_commands(check_id, profile, home=None, ubuntu=None):
    python = f"python{profile['python']}"
    node = profile["node"]
    nvm = ('export NVM_DIR="$HOME/.nvm"; '
           f'[ -s "$NVM_DIR/nvm.sh" ] || curl -fsSL {NVM_INSTALL} | bash; '
           f'. "$NVM_DIR/nvm.sh" && nvm install {node}')
    npm = f'. "$HOME/.nvm/nvm.sh" && nvm exec {node} npm install -g yarn@1'
    commands = {
        "build": [_cmd(APT + BUILD_PACKAGES, sudo=True)],
        "python": [
            _cmd(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"], sudo=True),
            _cmd(["apt-get", "update"], sudo=True),
            _cmd(APT + [python, f"{python}-dev", f"{python}-venv", "python3-pip"], sudo=True),
        ],
        "mariadb": [_cmd(APT + ["mariadb-server"], sudo=True)],
        "charset": [
            _cmd(["tee", CNF_PATH], sudo=True, input=CNF),
            _cmd(["systemctl", "restart", "mariadb"], sudo=True),
        ],
        "redis": [_cmd(APT + ["redis-server"], sudo=True)],
        "node": [_cmd(["bash", "-c", nvm])],
        "yarn": [_cmd(["bash", "-c", npm])],
        "wkhtmltopdf": [_cmd(APT + ["xvfb", "libfontconfig", "wkhtmltopdf"], sudo=True)],
    }
    if _version_key(ubuntu if ubuntu is not None else ubuntu_version()) >= (23, 4):
        commands["bench"] = [_cmd(APT + ["pipx"], sudo=True), _cmd(["pipx", "install", "frappe-bench"])]
    else:
        commands["bench"] = [_cmd(["pip3", "install", "--user", "frappe-bench"])]
    return commands[check_id]


def sudo_call(argv, password, input=None, passwordless=False):
    """The argv and stdin for running a command as root.

    When sudo asks for a password it reads the first line of stdin. When it does not
    (NOPASSWD), it reads nothing, so the password must not be sent or it would reach
    the command itself.
    """
    if passwordless:
        return ["sudo", "-n", *argv], input
    return ["sudo", "-S", "-k", "-p", "", *argv], password + "\n" + (input or "")


def sudo_is_passwordless():
    rc, _ = run_cmd(["sudo", "-n", "true"], timeout=20)
    return rc == 0


def check_sudo(password):
    if sudo_is_passwordless():
        return True
    argv, stdin = sudo_call(["true"], password)
    rc, _ = run_cmd(argv, input=stdin, timeout=20)
    return rc == 0


def install(check_id, profile, sudo_password, home=None):
    """Install or fix one prerequisite. Returns {"ok", "log", "error"}."""
    commands = install_commands(check_id, profile, home)
    if any(c["sudo"] for c in commands) and not check_sudo(sudo_password or ""):
        return {"ok": False, "log": "", "error": "wrong_sudo_password"}
    env = build_env(profile["node"], home)
    passwordless = sudo_is_passwordless()
    log = []
    for command in commands:
        argv, stdin = command["argv"], command["input"]
        log.append("$ " + " ".join(argv))
        if command["sudo"]:
            argv, stdin = sudo_call(argv, sudo_password or "", stdin, passwordless)
        rc, out = run_cmd(argv, env, input=stdin, timeout=1800)
        log.append(out.rstrip())
        if rc != 0:
            log.append(f"exit code {rc}")
            return {"ok": False, "log": "\n".join(log), "error": "command_failed"}
    return {"ok": True, "log": "\n".join(log), "error": None}


def verify_mariadb_root(password):
    env = dict(os.environ, MYSQL_PWD=password or "")
    rc, _ = run_cmd(["mariadb", "-uroot", "-e", "select 1"], env, timeout=20)
    return rc == 0


def root_password_sql(password):
    escaped = password.replace("\\", "\\\\").replace("'", "\\'")
    return (f"ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password "
            f"USING PASSWORD('{escaped}');\nFLUSH PRIVILEGES;\n")


def set_mariadb_root(sudo_password, password):
    """Give MariaDB's root user a password, through sudo. The SQL goes on stdin, not argv."""
    if not check_sudo(sudo_password or ""):
        return {"ok": False, "error": "wrong_sudo_password", "log": ""}
    argv, stdin = sudo_call(["mariadb"], sudo_password or "", root_password_sql(password), sudo_is_passwordless())
    rc, out = run_cmd(argv, input=stdin, timeout=30)
    if rc != 0:
        return {"ok": False, "error": "command_failed", "log": "MariaDB rejected the statement."}
    return {"ok": verify_mariadb_root(password), "error": None, "log": ""}
