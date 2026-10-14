import json
from pathlib import Path

from .graph import CycleError, toposort

VERSION_FIELDS = ("title", "python", "node")
APP_FIELDS = ("title", "description", "repo", "branches", "requires", "private")
PRESET_FIELDS = ("title", "description", "apps")


class CatalogError(ValueError):
    pass


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise CatalogError(f"cannot read catalog {path}: {exc}") from exc


def load(path, local_path=None):
    """Load the shipped catalog, plus the user's own apps and presets if they have any.

    The local file has the same shape, holds only `apps` and `presets`, and never leaves
    the user's machine. Its entries are marked "local" and override shipped ones.
    """
    data = _read(path)
    validate(data)
    if local_path and Path(local_path).exists():
        local = _read(local_path)
        if not isinstance(local, dict):
            raise CatalogError(f"{local_path}: must be an object")
        for section in ("apps", "presets"):
            for name, entry in (local.get(section) or {}).items():
                if isinstance(entry, dict):
                    entry = dict(entry, local=True)
                    entry.pop("default", None)
                data[section][name] = entry
        try:
            validate(data)
        except CatalogError as exc:
            raise CatalogError(f"{local_path}: {exc}") from exc
    return data


def _require_fields(kind, name, entry, fields):
    if not isinstance(entry, dict):
        raise CatalogError(f"{kind} '{name}' must be an object")
    for field in fields:
        if field not in entry:
            raise CatalogError(f"{kind} '{name}' is missing '{field}'")


def _default(entries, kind):
    defaults = [name for name, entry in entries.items() if entry.get("default")]
    if len(defaults) != 1:
        raise CatalogError(f"catalog needs exactly one default {kind}, found {len(defaults)}")
    return defaults[0]


def validate(data):
    if not isinstance(data, dict):
        raise CatalogError("catalog must be an object")
    for key in ("frappe_versions", "apps", "presets"):
        if not isinstance(data.get(key), dict) or not data[key]:
            raise CatalogError(f"catalog needs a non-empty '{key}' object")
    versions, apps, presets = data["frappe_versions"], data["apps"], data["presets"]

    for name, version in versions.items():
        _require_fields("Frappe version", name, version, VERSION_FIELDS)
    _default(versions, "Frappe version")

    if "frappe" not in apps:
        raise CatalogError("catalog needs an app named 'frappe'")
    for name, app in apps.items():
        _require_fields("app", name, app, APP_FIELDS)
        for branch_key in app["branches"]:
            if branch_key not in versions:
                raise CatalogError(f"app '{name}' has a branch for unknown Frappe version '{branch_key}'")
        for required in app["requires"]:
            if required not in apps:
                raise CatalogError(f"app '{name}' requires unknown app '{required}'")
    try:
        toposort(apps, {name: set(app["requires"]) for name, app in apps.items()})
    except CycleError as exc:
        raise CatalogError(str(exc)) from exc

    for name, preset in presets.items():
        _require_fields("preset", name, preset, PRESET_FIELDS)
        for app in preset["apps"]:
            if app not in apps:
                raise CatalogError(f"preset '{name}' names unknown app '{app}'")
    _default(presets, "preset")


def default_version(data):
    return _default(data["frappe_versions"], "Frappe version")


def default_preset(data):
    return _default(data["presets"], "preset")
