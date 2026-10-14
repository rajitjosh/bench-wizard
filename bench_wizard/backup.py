"""Find out which apps a Frappe database backup contains, without loading it."""
import gzip
import re
from dataclasses import dataclass, field

TABLE = "tabInstalled Application"
_CREATE = re.compile(r"^CREATE TABLE `([^`]+)`")
_COLUMN = re.compile(r"^\s*`([^`]+)`\s")
_INSERT = re.compile(r"^INSERT INTO `" + re.escape(TABLE) + r"`\s*(\(([^)]*)\))?\s*VALUES\s*", re.I)


class BackupError(ValueError):
    pass


@dataclass
class BackupInfo:
    apps: dict = field(default_factory=dict)  # app name -> version
    frappe_major: int = None
    partial: bool = False  # `bench backup --only/--exclude`; needs partial-restore


def _rows(sql):
    """Yield each (...) tuple of a VALUES list as a list of strings or None."""
    i, n = 0, len(sql)
    while i < n:
        if sql[i] != "(":
            i += 1
            continue
        i += 1
        row = []
        while i < n:
            if sql[i] == "'":
                i += 1
                buf = []
                while i < n:
                    ch = sql[i]
                    if ch == "\\" and i + 1 < n:
                        buf.append(sql[i + 1])
                        i += 2
                    elif ch == "'":
                        if sql[i + 1:i + 2] == "'":
                            buf.append("'")
                            i += 2
                        else:
                            i += 1
                            break
                    else:
                        buf.append(ch)
                        i += 1
                row.append("".join(buf))
            else:
                j = i
                while j < n and sql[j] not in ",)":
                    j += 1
                token = sql[i:j].strip()
                row.append(None if token.upper() == "NULL" else token)
                i = j
            if i < n and sql[i] == ",":
                i += 1
                continue
            i += 1  # closing parenthesis
            break
        yield row


def inspect(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    info = BackupInfo()
    columns, in_create, seen = [], False, False
    name_at = version_at = None  # set while inside an INSERT whose rows continue on later lines
    header_version = ""

    def take(sql):
        for row in _rows(sql):
            if len(row) > max(name_at, version_at) and row[name_at]:
                info.apps[row[name_at]] = row[version_at] or ""
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for number, line in enumerate(fh):
                if number < 10 and line.startswith("-- version = "):
                    header_version = line.split("=", 1)[1].strip()
                if number < 10 and line.startswith("-- Partial Backup"):
                    info.partial = True
                if name_at is not None:  # a row of the statement that began on an earlier line
                    take(line)
                    if line.rstrip().endswith(";"):
                        name_at = None
                    continue
                create = _CREATE.match(line)
                if create:
                    if seen:
                        break
                    in_create = create.group(1) == TABLE
                    seen = in_create
                    continue
                if in_create:
                    column = _COLUMN.match(line)
                    if column:
                        columns.append(column.group(1))
                    elif line.startswith(")"):
                        in_create = False
                    continue
                if not seen:
                    continue
                insert = _INSERT.match(line)
                if not insert:
                    continue
                names = re.findall(r"`([^`]+)`", insert.group(2)) if insert.group(2) else columns
                if "app_name" not in names or "app_version" not in names:
                    continue
                name_at, version_at = names.index("app_name"), names.index("app_version")
                take(line[insert.end():])
                if line.rstrip().endswith(";"):
                    name_at = None
    except (OSError, EOFError) as exc:
        raise BackupError(f"cannot read backup {path}: {exc}") from exc

    match = re.match(r"(\d+)\.", info.apps.get("frappe") or header_version)
    info.frappe_major = int(match.group(1)) if match else None
    return info
