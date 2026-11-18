#!/usr/bin/env python3
"""
frappe_upgrade_scan.py - upgrade-impact scanner for a Frappe/ERPNext custom app.

What it does
  1. Inventories everything the custom app changes in core (hooks, overrides,
     subclassed controllers, custom fields, property setters, patches, imports).
  2. Resolves every core import / dotted method path / overridden method against
     the TARGET framework source (and the SOURCE version, when given) and reports
     what was removed, moved, or changed upstream.
  3. Diffs the schema of every standard DocType the app touches (fields removed,
     type changes, new mandatory fields, custom-field name collisions).
  4. Runs the version-specific breaking-change rule pack (default: v15 -> v16).

Usage
  python3 frappe_upgrade_scan.py --app /path/to/custom_app_repo \
      --source frappe=/trees/frappe-v15 --source erpnext=/trees/erpnext-v15 \
      --target frappe=/trees/frappe-v16 --target erpnext=/trees/erpnext-v16 \
      --rules v15-v16 --out /path/to/report_dir

  --source is optional (without it, drift/diff checks are skipped and only
  target-existence checks + rule pack run). Add more trees (hrms, payments,
  india_compliance ...) with extra --source/--target name=path pairs.

Outputs  <out>/findings.json, <out>/inventory.json, <out>/report.md
Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SKIP_PARTS = {"node_modules", ".git", "dist", "__pycache__", ".venv", "venv", "build", ".mypy_cache", ".ruff_cache"}
CODE_EXT = {".py", ".js", ".ts", ".vue", ".html", ".jinja", ".json", ".toml", ".txt", ".cfg", ".md"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

# --------------------------------------------------------------------------------------
# Rule packs (regex, line-based). Each rule: id, sev, cat, ext (tuple), rx, msg, fix, ref,
# optional path (regex on posix path), optional skip (regex; line ignored if it matches)
# --------------------------------------------------------------------------------------
FW = "https://github.com/frappe/frappe/wiki/Migrating-to-version-16"
QB = "https://github.com/frappe/frappe/wiki/query-builder-migration"
EN = "https://github.com/frappe/erpnext/wiki/Migration-Guide-To-ERPNext-Version-16"
SQL_FN = r"(?:sum|count|avg|min|max|ifnull|coalesce|concat|concat_ws|timestamp|date_format|group_concat|year|month|day|date|abs|round|if|case|now|curdate|datediff|length|lower|upper|trim|substring|cast)"

RULES_V15_V16 = [
    # ---- Query builder (get_all / get_list / get_value / qb.get_query) -------------
    dict(id="QB-FUNC", sev="high", cat="Query builder", ext=(".py",),
         rx=rf"""["']\s*{SQL_FN}\s*\([^"']*\)(\s+as\s+\w+)?\s*["']""",
         msg="Raw SQL function string (e.g. 'sum(x) as y'). get_all/get_list/get_value/qb.get_query now use the query builder and reject raw SQL in fields, filters and order_by.",
         fix='fields=[{"SUM": "actual_qty", "as": "qty"}] or pypika functions (frappe.query_builder.functions.Sum(dt.field).as_("qty")). Ignore if the string is only passed to frappe.db.sql().',
         ref=QB),
    dict(id="QB-DISTINCT", sev="high", cat="Query builder", ext=(".py",),
         rx=r"""["']\s*distinct\s+[`\w]""",
         msg="'distinct <field>' inside fields list is no longer accepted.",
         fix="Pass fields=['batch_no'], distinct=True.", ref=QB),
    dict(id="QB-LITERAL", sev="medium", cat="Query builder", ext=(".py",),
         rx=r"""["']\s*['"][^'"]+['"]\s+as\s+\w+\s*["']""",
         msg="Literal value selected as a field (\"'X' as doctype\").",
         fix='from pypika.terms import ValueWrapper; fields=[ValueWrapper("X").as_("doctype")]', ref=QB),
    dict(id="QB-FILTER-FN", sev="high", cat="Query builder", ext=(".py",),
         rx=rf"""["']\s*{SQL_FN}\s*\(\s*`?\w+`?\s*,[^"']*\)\s*["']\s*[:,]""",
         msg="SQL function used as a filter key/field (e.g. 'ifnull(x, 0)').",
         fix='filters=[[IfNull(Field("x"), 0), "=", 0]] using frappe.query_builder.Field / functions.IfNull.', ref=QB),
    dict(id="QB-ORDERBY", sev="medium", cat="Query builder", ext=(".py",),
         rx=rf"""order_by\s*=\s*["'][^"']*{SQL_FN}\s*\(""",
         msg="order_by expression contains a SQL function; complex order_by may need frappe.qb directly.",
         fix="Rewrite with frappe.qb ... .orderby(Timestamp(t.posting_date, t.posting_time)).", ref=QB),
    dict(id="QB-RUN0", sev="medium", cat="Query builder", ext=(".py",),
         rx=r"""\brun\s*=\s*(0|False)\b""",
         msg="run=False/0 now returns a QueryBuilder object, not an SQL string.",
         fix="Call .get_sql() on the returned object if a string is needed.", ref=QB),
    dict(id="QB-NE-NONE", sev="medium", cat="Query builder", ext=(".py",),
         rx=r"""["']!=["']\s*,\s*None\b""",
         msg="'!= None' filter now correctly generates IS NOT NULL - results may change.",
         fix="Confirm the intended semantics; prefer ['is', 'set'].", ref=FW),
    dict(id="JSON-FILTER-5", sev="high", cat="Query builder", ext=(".json",),
         rx=r"""\[\\?"[^"\\]+\\?",\s*\\?"[^"\\]+\\?",\s*\\?"[^"\\]+\\?",\s*[^\],]+,\s*(false|true)\s*\]""",
         msg="5-element filter in Number Card / Dashboard Chart / report JSON. The query builder rejects the trailing boolean.",
         fix='[["Employee","status","=","Active"]] - drop the 5th element (also in DB records via a patch).', ref=QB),

    # ---- Database API -----------------------------------------------------------------
    dict(id="DB-SETVALUE-SINGLE", sev="high", cat="Database API", ext=(".py",),
         rx=r"""db\.set_value\(\s*["'][^"']+["']\s*,\s*None\b""",
         msg="frappe.db.set_value(<Single>, None, ...) is no longer accepted.",
         fix="frappe.db.set_single_value(doctype, field, value)", ref="https://github.com/frappe/frappe/pull/19481"),
    dict(id="DB-SETVALUE-SINGLE2", sev="medium", cat="Database API", ext=(".py",),
         rx=r"""db\.set_value\(\s*(["'])([^"']+)\1\s*,\s*\1\2\1""",
         msg="set_value(X, X, ...) - looks like a Single DocType write.",
         fix="frappe.db.set_single_value(doctype, field, value)", ref="https://github.com/frappe/frappe/pull/19481"),
    dict(id="DB-SINGLE-CAST", sev="medium", cat="Database API", ext=(".py",),
         rx=r"""get_value\([^)]*\)\s*(==|!=)\s*["']\d+(\.\d+)?["']""",
         msg="db.get_value on Single DocTypes now returns typed values (int/float), not strings.",
         fix="Compare with 1 / 0 (numbers) instead of '1'.", ref=FW),
    dict(id="DB-COMMIT", sev="medium", cat="Transactions", ext=(".py",),
         rx=r"""frappe\.db\.commit\(\)""",
         msg="frappe.db.commit() is not allowed inside document hooks (doc_events) in v16. See HOOK-COMMIT findings for confirmed cases.",
         fix="Remove; let the request/job transaction commit. Use frappe.enqueue(..., enqueue_after_commit=True) for side effects.", ref=FW),
    dict(id="DB-VALUE-CACHE", sev="high", cat="Database API", ext=(".py",),
         rx=r"""db\.value_cache""",
         msg="db.value_cache is now a nested defaultdict (doctype -> name -> field).",
         fix='frappe.db.value_cache["User"]["Guest"].pop("full_name", None)', ref=FW),
    dict(id="DB-COUNT-CACHE", sev="low", cat="Database API", ext=(".py",),
         rx=r"""db\.count\([^)]*cache\s*=\s*True""",
         msg="db.count(cache=True) is now per-transaction (value_cache), not Redis.",
         fix="Use frappe.cache explicitly if cross-request caching is needed.", ref=FW),
    dict(id="GETDOC-KWARGS", sev="high", cat="Document API", ext=(".py",),
         rx=r"""frappe\.get_doc\(\s*["'][^"']+["']\s*,\s*[^,()]+,\s*(?!for_update\b|check_permission\b)\w+\s*=""",
         msg="get_doc(doctype, name, field=value) no longer applies the kwargs to the document.",
         fix="doc = frappe.get_doc(dt, name); doc.update({...})", ref=FW),
    dict(id="META-VALID-COLS", sev="low", cat="Document API", ext=(".py",),
         rx=r"""get_valid_columns\(""",
         msg="meta.get_valid_columns() now excludes virtual fields.",
         fix="Add virtual fields explicitly if needed.", ref="https://github.com/frappe/frappe/pull/27553"),
    dict(id="SITE-CACHE", sev="low", cat="Caching", ext=(".py",),
         rx=r"""@site_cache""",
         msg="@site_cache no longer supports unhashable args (dict/list).",
         fix="Ensure decorated functions only take hashable arguments.", ref=FW),
    dict(id="SENDMAIL-NOW", sev="medium", cat="Transactions", ext=(".py",),
         rx=r"""sendmail\([^)]*now\s*=\s*(True|1)""",
         msg="frappe.sendmail(now=True) no longer commits the transaction implicitly.",
         fix="Commit explicitly only where truly required (not in doc hooks).", ref=FW),

    # ---- Permissions -----------------------------------------------------------------
    dict(id="PERM-RAISE", sev="high", cat="Permissions", ext=(".py",),
         rx=r"""has_permission\([^)]*raise_exception\s*=""",
         msg="frappe.permission.has_permission no longer accepts raise_exception.",
         fix="Use print_logs=..., or frappe.has_permission(..., throw=True).", ref="https://github.com/frappe/frappe/pull/24266"),

    # ---- Navigation / routes -----------------------------------------------------------
    dict(id="ROUTE-APP", sev="medium", cat="Routes & UI", ext=(".py", ".js", ".ts", ".vue", ".html", ".jinja", ".json"),
         rx=r"""["'`(=]\s*/app(/|["'`?#])""",
         msg="Desk moved from /app to /desk. Hard-coded /app/... links break (redirects may exist but don't rely on them).",
         fix="Use frappe.utils.get_link_to_form / get_url_to_form (py) or frappe.set_route / frappe.utils.get_form_link (js).",
         ref="https://github.com/frappe/frappe/pull/34565"),
    dict(id="ROUTE-APPS-PAGE", sev="low", cat="Routes & UI", ext=(".py", ".js", ".vue", ".html"),
         rx=r"""["'`]/apps["'`/]""",
         msg="/apps page is deprecated (replaced by the Desktop screen).",
         fix="Point to /desk; configure add_to_apps_screen in hooks.", ref=FW),
    dict(id="POST-ONLY", sev="medium", cat="API", ext=(".py", ".js", ".ts", ".vue", ".html"),
         rx=r"""(api/method/)?(logout|web_logout|upload_file|frappe\.www\.login\.send_login_link)\b["'?]""",
         path=r".*", skip=r"def (logout|upload_file)",
         msg="logout / web_logout / upload_file / send_login_link now require POST.",
         fix="Switch any GET callers (fetch, requests.get, <a href>) to POST.", ref=FW),

    # ---- Translations -------------------------------------------------------------------
    dict(id="TRANS-REMOVED", sev="high", cat="Translations", ext=(".py",),
         rx=r"""(frappe\.get_lang_dict|translate\.get_dict\b|get_lang_js|get_dict_from_hooks)""",
         msg="Removed translation APIs.", fix="Use _() and app translation files.", ref="https://github.com/frappe/frappe/pull/22962"),
    dict(id="TRANS-HOOK", sev="high", cat="Translations", ext=(".py",), path=r"hooks\.py$",
         rx=r"""get_translated_dict""",
         msg="get_translated_dict hook removed (view-specific translations).",
         fix="Move strings to the app's translation files.", ref=FW),
    dict(id="TRANS-COUNTRY", sev="low", cat="Translations", ext=(".py",),
         rx=r"""country_info\.get_translated_dict""",
         msg="Deprecated; only returns country names now.", fix="Use get_translated_countries.", ref=FW),

    # ---- Modules moved out of core -----------------------------------------------------
    dict(id="MOD-MOVED", sev="high", cat="Removed modules", ext=(".py", ".js", ".json", ".vue", ".html"),
         rx=r"""(Energy Point (Log|Rule|Settings)|energy_point|\bNewsletter\b|newsletter\.|\bBlog (Post|Category|Settings)\b|\bBlogger\b|blog_post|S3 Backup Settings|Google Drive\b|google_drive|Dropbox Settings|dropbox_settings|s3_backup_settings)""",
         msg="Module moved out of Frappe core (Energy Points -> frappe/eps, Newsletter -> frappe/newsletter, Blog -> frappe/blog, Backup integrations -> frappe/offsite_backups).",
         fix="Add the new app to the bench + required_apps, or drop the dependency.", ref=FW),
    dict(id="MOD-TXN-LOG", sev="high", cat="Removed modules", ext=(".py", ".js", ".json"),
         rx=r"""Transaction Log|transaction_log""",
         msg="Transaction Log DocType and report removed (frappe + erpnext).", fix="Remove usage.", ref=EN),
    dict(id="MOD-GEOIP", sev="medium", cat="Removed modules", ext=(".py",),
         rx=r"""geo_ip|geoip|get_geo_from_ip|get_geo_ip_country""",
         msg="GeoIP helpers removed from the framework.", fix="Use an external service.", ref=FW),

    # ---- Tests ------------------------------------------------------------------------
    dict(id="TEST-INTEST", sev="low", cat="Tests", ext=(".py",),
         rx=r"""flags\.in_test""", skip=r"toggle_test_mode",
         msg="frappe.flags.in_test is deprecated.", fix="Read frappe.in_test; set via frappe.tests.utils.toggle_test_mode.", ref=FW),
    dict(id="TEST-CASE", sev="low", cat="Tests", ext=(".py",),
         rx=r"""FrappeTestCase|frappe\.tests\.utils import""",
         msg="FrappeTestCase deprecated (removed in v17).",
         fix="from frappe.tests import IntegrationTestCase, UnitTestCase", ref=FW),
    dict(id="TEST-DEPS", sev="low", cat="Tests", ext=(".py",),
         rx=r"""^\s*(test_dependencies|test_ignore)\s*=""",
         msg="Renamed test globals.", fix="EXTRA_TEST_RECORD_DEPENDENCIES / IGNORE_TEST_RECORD_DEPENDENCIES", ref=FW),

    # ---- Misc framework ----------------------------------------------------------------
    dict(id="SANITIZE", sev="low", cat="Misc", ext=(".py",),
         rx=r"""import bleach|from bleach|sanitize_html\(""",
         msg="sanitize_html now uses nh3 (strips forbidden tags instead of escaping). bleach is no longer a framework dependency.",
         fix="Declare bleach yourself if imported; re-check sanitized output.", ref=FW),
    dict(id="COUNTRY-CODE", sev="low", cat="Misc", ext=(".py", ".json"),
         rx=r"""new_doc\(\s*["']Country["']|["']doctype["']\s*:\s*["']Country["']""",
         msg="Country now requires a valid ISO 3166 alpha-2 code.", fix="Always set code.", ref=FW),
    dict(id="BENCH-VERSION", sev="low", cat="Tooling", ext=(".py", ".sh", ".yml", ".yaml"),
         rx=r"""bench\s+version(?!\s+-f)""",
         msg="bench version default output changed to 'plain' (adds branch + commit).", fix="Use bench version -f legacy in scripts.", ref=FW),

    # ---- Python 3.14 / Node 24 runtime ---------------------------------------------------
    dict(id="PY-REMOVED-STDLIB", sev="high", cat="Runtime (Python 3.14)", ext=(".py",),
         rx=r"""^\s*(import|from)\s+(distutils|imp|asynchat|asyncore|smtpd|cgi|cgitb|crypt|telnetlib|nntplib|pipes|uu|xdrlib|audioop|chunk|imghdr|mailcap|msilib|nis|ossaudiodev|sndhdr|spwd|sunau|lib2to3)\b""",
         msg="Stdlib module removed before Python 3.14 (v16 requires Python 3.14+).", fix="Replace with the maintained alternative.", ref=FW),
    dict(id="PY-UTCNOW", sev="low", cat="Runtime (Python 3.14)", ext=(".py",),
         rx=r"""datetime\.utcnow\(|utcfromtimestamp\(""",
         msg="Deprecated datetime APIs (warnings on 3.12+).", fix="datetime.now(timezone.utc)", ref="https://docs.python.org/3/whatsnew/3.12.html"),
    dict(id="PKG-FRAPPE-PIN", sev="high", cat="Packaging", ext=(".toml", ".py", ".cfg", ".txt"),
         rx=r"""frappe[^\n]*<\s*16|frappe[^\n]*~=\s*15|erpnext[^\n]*<\s*16""",
         msg="App pins frappe/erpnext below 16 - bench will refuse or the app will be mis-resolved.",
         fix='[tool.bench.frappe-dependencies] frappe = ">=16.0.0,<17.0.0"', ref="https://immanuelraj.dev/frappe-v15-to-v16-beta-migration-guide/"),
    dict(id="PKG-PY-PIN", sev="medium", cat="Packaging", ext=(".toml",),
         rx=r"""requires-python\s*=\s*["'][^"']*(<\s*3\.1[0-3]|~=\s*3\.1[0-3]|==\s*3\.)""",
         msg="requires-python excludes 3.14.", fix='requires-python = ">=3.14"', ref=FW),
    dict(id="PKG-VUE2", sev="high", cat="Runtime (Node 24)", ext=(".json",), path=r"package\.json$",
         rx=r""""vue"\s*:\s*"[\^~]?2\.""",
         msg="Vue 2 frontend - v16 desk ships Vue 3.", fix="Migrate components to Vue 3 / frappe-ui.", ref=FW),
    dict(id="PKG-NODE", sev="medium", cat="Runtime (Node 24)", ext=(".json",), path=r"package\.json$",
         rx=r""""node"\s*:\s*"[^"]*(<\s*2[0-3]|\b1\d\.|\b2[0-3]\.)""",
         msg="engines.node excludes Node 24.", fix='"node": ">=24"', ref=FW),

    # ---- ERPNext v16 -----------------------------------------------------------------------
    dict(id="EN-ITEMWISE-TAX", sev="high", cat="ERPNext", ext=(".py", ".js", ".json", ".html", ".jinja"),
         rx=r"""item_wise_tax_detail\b""",
         msg="item_wise_tax_detail JSON replaced by the 'Item Wise Tax Detail' child table (item_wise_tax_details). Affects tax logic, integrations, print formats, reports.",
         fix="Populate doc._item_wise_tax_details with frappe._dict(item=row, tax=tax_row, rate, amount, taxable_amount) and set dont_recompute_tax=1; reports query 'Item Wise Tax Detail'.",
         ref="https://github.com/frappe/erpnext/wiki/Item-Wise-Tax-Details:-JSON-%E2%86%92-Child-Table"),
    dict(id="EN-TDS", sev="medium", cat="ERPNext", ext=(".py", ".js"),
         rx=r"""tax_withholding_category\.|get_party_tax_withholding_details|get_tax_withholding_details|tax_withholding_details""",
         msg="Tax Withholding refactored around the new 'Tax Withholding Entry' child table.",
         fix="Re-test TDS/TCS customisations against the new flow.", ref="https://github.com/frappe/erpnext/wiki/Tax-Withholding-Entry"),
    dict(id="EN-REMOVED-METHODS", sev="high", cat="ERPNext", ext=(".py", ".js"),
         rx=r"""bank_account\.make_bank_account|pricing_rule\.make_pricing_rule|erpnext\.utils\.make_pricing_rule""",
         msg="Whitelisted helper removed.", fix="Use frappe.new_doc('Bank Account'/'Pricing Rule') and set fields.", ref=EN),
    dict(id="EN-TIMESHEET", sev="medium", cat="ERPNext", ext=(".py", ".js"),
         rx=r"""timesheets|add_timesheet_data""",
         msg="Sales Invoices created via API no longer auto-pull unbilled timesheets.",
         fix="Call Sales Invoice method add_timesheet_data explicitly if relied upon.", ref=EN),
]

RULE_PACKS = {"v15-v16": RULES_V15_V16}

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def iter_files(root: Path, exts=CODE_EXT):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in exts and not (set(p.parts) & SKIP_PARTS):
            yield p


_parse_cache: dict = {}


PARSE_DEGRADED: set = set()


def parse(path: Path):
    """Parse a file. If the running interpreter is older than the code (e.g. v16 uses
    Python 3.14 `type X = ...`), neutralise offending lines one by one and retry, so
    symbol resolution still works. Degraded files are reported."""
    if path not in _parse_cache:
        src = path.read_text(encoding="utf-8", errors="replace")
        lines, tree = src.splitlines(), None
        for _ in range(60):
            try:
                tree = ast.parse("\n".join(lines))
                break
            except (SyntaxError, ValueError) as e:
                ln = getattr(e, "lineno", None)
                if not ln or ln > len(lines):
                    break
                bad = lines[ln - 1]
                lines[ln - 1] = bad[: len(bad) - len(bad.lstrip())] + "pass  # [scanner] unparsable on this Python"
                PARSE_DEGRADED.add(str(path))
        _parse_cache[path] = (tree, "\n".join(lines) if tree else "")
    return _parse_cache[path]


def top_defs(path: Path) -> dict:
    """name -> ast node for top-level defs/classes/assigns/imports (incl. inside if/try)."""
    tree, _ = parse(path)
    out = {}
    if tree is None:
        return out

    def visit(body):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[n.name] = n
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = n
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                out[n.target.id] = n
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    if a.name == "*" and isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                        out.setdefault("__star__", []).append(n.module)
                    else:
                        out[(a.asname or a.name).split(".")[0]] = n
            elif isinstance(n, (ast.If, ast.Try)):
                visit(n.body)
                for h in getattr(n, "handlers", []):
                    visit(h.body)
                visit(getattr(n, "orelse", []))
    visit(tree.body)
    return out


def expand(d):
    """Hook dicts may use tuple keys: {("Sales Invoice", "Purchase Invoice"): ...}. Yield (dt, value)."""
    if not isinstance(d, dict):
        return
    for k, v in d.items():
        for kk in (k if isinstance(k, (tuple, list)) else (k,)):
            yield kk, v


def norm(node) -> str:
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def sig_shape(fn):
    """Parameter names/kinds/default-count, ignoring annotations."""
    a = fn.args
    return (tuple(x.arg for x in getattr(a, "posonlyargs", [])), tuple(x.arg for x in a.args), a.vararg.arg if a.vararg else None,
            tuple(x.arg for x in a.kwonlyargs), a.kwarg.arg if a.kwarg else None, len(a.defaults), sum(d is not None for d in a.kw_defaults))


def sig(fn) -> str:
    try:
        return ast.unparse(fn.args)
    except Exception:
        return norm(fn.args)


class Tree:
    """An installed app source tree (repo root containing <pkg>/__init__.py)."""

    def __init__(self, name: str, path: str):
        p = Path(path).expanduser().resolve()
        if (p / name / "__init__.py").exists():
            self.root = p
        elif (p / "__init__.py").exists() and p.name == name:
            self.root = p.parent
        else:
            raise SystemExit(f"[error] {path} does not look like the '{name}' repo (no {name}/__init__.py)")
        self.name = name
        self.modules: dict[str, Path] = {}
        for f in (self.root / name).rglob("*.py"):
            if set(f.parts) & SKIP_PARTS:
                continue
            parts = list(f.relative_to(self.root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            self.modules[".".join(parts)] = f
        self._doctypes = None

    @property
    def doctypes(self) -> dict:
        if self._doctypes is None:
            self._doctypes = {}
            for j in (self.root / self.name).rglob("doctype/*/*.json"):
                if j.stem != j.parent.name:
                    continue
                try:
                    d = json.loads(j.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(d, dict) and d.get("doctype") == "DocType" and d.get("name"):
                    d["_path"] = j
                    self._doctypes[d["name"]] = d
        return self._doctypes


class TreeSet:
    def __init__(self, trees: list[Tree]):
        self.trees = {t.name: t for t in trees}

    def __bool__(self):
        return bool(self.trees)

    def owns(self, dotted: str) -> bool:
        return dotted.split(".")[0] in self.trees

    def module(self, dotted: str):
        t = self.trees.get(dotted.split(".")[0])
        return t.modules.get(dotted) if t else None

    def resolve(self, dotted: str, depth=0):
        """Resolve a dotted path -> ('module', path) | ('def', path, node) | None.
        Follows re-exports (from x import y) up to a few hops."""
        if depth > 6 or not self.owns(dotted):
            return None
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            mod = ".".join(parts[:i])
            f = self.module(mod)
            if not f:
                continue
            rest = parts[i:]
            if not rest:
                return ("module", f)
            defs = top_defs(f)
            node = defs.get(rest[0])
            if node is None:
                for star in defs.get("__star__", []):
                    r = self.resolve(".".join([star] + rest), depth + 1)
                    if r:
                        return r
                return None
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for a in node.names:
                    if (a.asname or a.name) == rest[0]:
                        return self.resolve(".".join([node.module, a.name] + rest[1:]), depth + 1)
            if isinstance(node, ast.Import):
                return ("def", f, node)
            if len(rest) == 1:
                return ("def", f, node)
            if isinstance(node, ast.ClassDef) and len(rest) == 2:
                m = class_method(self, f, node, rest[1])
                return ("def", m[0], m[1]) if m else None
            return ("def", f, node)  # deeper attribute access - accept
        return None

    def doctype(self, name):
        for t in self.trees.values():
            d = t.doctypes.get(name)
            if d:
                return d
        return None


def resolve_name_in_file(ts: TreeSet, path: Path, name: str):
    """Resolve a bare name used in `path` to (file, ClassDef) via local defs/imports."""
    defs = top_defs(path)
    node = defs.get(name)
    if isinstance(node, ast.ClassDef):
        return path, node
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        for a in node.names:
            if (a.asname or a.name) == name:
                r = ts.resolve(f"{node.module}.{a.name}")
                if r and r[0] == "def" and isinstance(r[2], ast.ClassDef):
                    return r[1], r[2]
    return None


def class_method(ts: TreeSet, path: Path, cls: ast.ClassDef, method: str, depth=0):
    """Find method in class or its bases (MRO-ish). Returns (file, FunctionDef, owner_class_name)."""
    if depth > 8:
        return None
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method:
            return path, n, cls.name
    for b in cls.bases:
        bname = b.id if isinstance(b, ast.Name) else (b.attr if isinstance(b, ast.Attribute) else None)
        if not bname:
            continue
        if isinstance(b, ast.Attribute):
            r = ts.resolve(ast.unparse(b))
            hit = (r[1], r[2]) if r and r[0] == "def" and isinstance(r[2], ast.ClassDef) else None
        else:
            hit = resolve_name_in_file(ts, path, bname)
        if hit:
            m = class_method(ts, hit[0], hit[1], method, depth + 1)
            if m:
                return m
    return None


def class_dotted(ts: TreeSet, path: Path, cls_name: str):
    for t in ts.trees.values():
        for mod, f in t.modules.items():
            if f == path:
                return f"{mod}.{cls_name}"
    return None


# --------------------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------------------

class Scanner:
    def __init__(self, app_root: Path, src: TreeSet, tgt: TreeSet, rules):
        self.root = app_root
        self.src, self.tgt, self.rules = src, tgt, rules
        self.findings: list[dict] = []
        self.inventory: dict = {}
        hooks = [p for p in app_root.glob("*/hooks.py")] + [p for p in app_root.glob("hooks.py")]
        if not hooks:
            raise SystemExit(f"[error] no hooks.py found under {app_root}")
        self.hooks_path = hooks[0]
        self.pkg_dir = self.hooks_path.parent
        self.app = self.pkg_dir.name
        self.app_tree = Tree(self.app, str(self.pkg_dir.parent))
        # app's own modules resolve in both worlds (for intermediate classes)
        self.src_all = TreeSet(list(src.trees.values()) + [self.app_tree]) if src else None
        self.tgt_all = TreeSet(list(tgt.trees.values()) + [self.app_tree]) if tgt else None
        self.core_names = set(src.trees) | set(tgt.trees) | {"frappe", "erpnext", "hrms", "payments", "india_compliance", "lending"}

    # ---------- findings ----------
    def add(self, id, sev, cat, msg, file=None, line=None, fix="", ref="", snippet=""):
        rel = str(Path(file).relative_to(self.root)) if file and str(file).startswith(str(self.root)) else (str(file) if file else "")
        self.findings.append(dict(id=id, severity=sev, category=cat, message=msg, file=rel, line=line,
                                  snippet=snippet.strip()[:220], fix=fix, ref=ref))

    # ---------- inventory ----------
    def read_hooks(self) -> dict:
        tree, _ = parse(self.hooks_path)
        h = {}
        for n in (tree.body if tree else []):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                try:
                    h[n.targets[0].id] = ast.literal_eval(n.value)
                except Exception:
                    h[n.targets[0].id] = "<non-literal>"
        return h

    def app_func(self, dotted: str):
        """Resolve dotted path inside the custom app -> (file, node)."""
        r = TreeSet([self.app_tree]).resolve(dotted)
        return (r[1], r[2]) if r and r[0] == "def" else None

    def build_inventory(self):
        h = self.hooks = self.read_hooks()
        inv = self.inventory
        inv["app"] = self.app
        inv["hooks_keys"] = sorted(h.keys())
        for k in ("doc_events", "override_doctype_class", "extend_doctype_class", "override_whitelisted_methods",
                  "doctype_js", "doctype_list_js", "doctype_tree_js", "doctype_calendar_js", "has_permission",
                  "permission_query_conditions", "scheduler_events", "fixtures", "required_apps",
                  "add_to_apps_screen", "override_doctype_dashboards", "standard_queries", "jinja",
                  "app_include_js", "app_include_css", "boot_session", "on_session_creation", "on_login",
                  "before_request", "after_request", "before_job", "after_job", "website_route_rules"):
            if k in h:
                inv[k] = h[k]
        # patches
        pt = self.pkg_dir / "patches.txt"
        inv["patches"] = [l.strip() for l in pt.read_text().splitlines() if l.strip() and not l.startswith("#")] if pt.exists() else []
        # fixtures on disk
        fx = {}
        for j in (self.pkg_dir / "fixtures").glob("*.json") if (self.pkg_dir / "fixtures").exists() else []:
            try:
                data = json.loads(j.read_text(encoding="utf-8"))
                fx[j.name] = data if isinstance(data, list) else []
            except Exception:
                fx[j.name] = []
        self.fixtures = fx
        inv["fixture_files"] = {k: len(v) for k, v in fx.items()}
        # own doctypes
        inv["own_doctypes"] = sorted(self.app_tree.doctypes)
        # core imports
        imports = defaultdict(set)
        self.core_imports = []  # (file, line, module, name|None)
        for f in iter_files(self.pkg_dir, {".py"}):
            tree, _ = parse(f)
            if not tree:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module and n.module.split(".")[0] in self.core_names:
                    for a in n.names:
                        self.core_imports.append((f, n.lineno, n.module, a.name))
                        imports[n.module].add(a.name)
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name.split(".")[0] in self.core_names and "." in a.name:
                            self.core_imports.append((f, n.lineno, a.name, None))
                            imports[a.name].add("*")
        inv["core_imports"] = {k: sorted(v) for k, v in sorted(imports.items())}
        # dotted method strings (frappe.call / enqueue / get_attr)
        self.dotted_refs = []
        rx = re.compile(r"""(?:method\s*[:=]\s*|(?:frappe\.)?(?:call|xcall|enqueue|get_attr|get_hooks)\(\s*)["']((?:%s)\.[\w.]+)["']""" % "|".join(sorted(self.core_names)))
        for f in iter_files(self.pkg_dir, {".py", ".js", ".ts", ".vue"}):
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for m in rx.finditer(line):
                    self.dotted_refs.append((f, i, m.group(1)))
        inv["core_method_refs"] = sorted({d for _, _, d in self.dotted_refs})
        # subclassed core classes
        self.subclasses = []  # (file, ClassDef, base_dotted)
        for f in iter_files(self.pkg_dir, {".py"}):
            tree, _ = parse(f)
            if not tree:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef):
                    for b in n.bases:
                        bd = self._base_dotted(f, b)
                        if bd and bd.split(".")[0] in self.core_names:
                            self.subclasses.append((f, n, bd))
        inv["core_subclasses"] = [f"{self._dotted_of(f)}.{c.name}({b})" for f, c, b in self.subclasses]
        # touched standard doctypes
        touched = defaultdict(set)
        for k in ("doc_events", "override_doctype_class", "extend_doctype_class", "doctype_js", "doctype_list_js",
                  "doctype_tree_js", "doctype_calendar_js", "has_permission", "permission_query_conditions",
                  "override_doctype_dashboards"):
            for dt, _ in expand(h.get(k)):
                if dt != "*":
                    touched[dt].add(k)
        for row in fx.get("custom_field.json", []):
            if row.get("dt"):
                touched[row["dt"]].add("custom_field")
        for row in fx.get("property_setter.json", []):
            if row.get("doc_type"):
                touched[row["doc_type"]].add("property_setter")
        rx_dt = re.compile(r"""(?:get_doc|get_all|get_list|new_doc|get_value|set_value|exists|get_cached_doc|get_cached_value|get_single_value|count|get_meta|DocType|delete_doc|get_last_doc|form\.on)\(\s*["']([A-Z][\w &\-]+)["']""")
        for f in iter_files(self.pkg_dir, {".py", ".js"}):
            for m in rx_dt.finditer(f.read_text(encoding="utf-8", errors="replace")):
                touched[m.group(1)].add("code")
        own = set(self.app_tree.doctypes)
        self.touched = {k: v for k, v in touched.items() if k not in own}
        inv["touched_standard_doctypes"] = {k: sorted(v) for k, v in sorted(self.touched.items())}

    def _dotted_of(self, f: Path):
        # f may be in a core tree too: following an override's base class leads into frappe's own files
        roots = [self.app_tree.root] + [t.root for ts in (self.tgt_all, self.src_all) if ts for t in ts.trees.values()]
        root = next((r for r in roots if r == f or r in f.parents), None)
        if root is None:
            return ""
        parts = list(f.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _base_dotted(self, f: Path, b):
        if isinstance(b, ast.Attribute):
            try:
                return ast.unparse(b)
            except Exception:
                return None
        if isinstance(b, ast.Name):
            node = top_defs(f).get(b.id)
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for a in node.names:
                    if (a.asname or a.name) == b.id:
                        return f"{node.module}.{a.name}"
        return None

    # ---------- checks: rules ----------
    def run_rules(self):
        compiled = [(r, re.compile(r["rx"], re.I if r["id"].startswith(("MOD", "QB-FUNC", "QB-FILTER", "QB-ORDER", "QB-DIST")) else 0),
                     re.compile(r["path"]) if r.get("path") else None,
                     re.compile(r["skip"]) if r.get("skip") else None) for r in self.rules]
        for f in iter_files(self.root):
            posix = f.as_posix()
            text = f.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            for r, rx, prx, skip in compiled:
                if f.suffix not in r["ext"] or (prx and not prx.search(posix)):
                    continue
                for i, line in enumerate(lines, 1):
                    if len(line) > 4000:
                        line = line[:4000]
                    if rx.search(line) and not (skip and skip.search(line)):
                        if f.suffix == ".py" and line.lstrip().startswith("#"):
                            continue
                        self.add(r["id"], r["sev"], r["cat"], r["msg"], f, i, r.get("fix", ""), r.get("ref", ""), line)
        # DocType sort order (structural)
        for name, d in self.app_tree.doctypes.items():
            if d.get("sort_field", "modified") == "modified" and not d.get("istable") and not d.get("issingle"):
                self.add("SORT-DOCTYPE", "low", "Default sort", f"DocType '{name}' sorts by 'modified'. v16 default is 'creation'; keeping 'modified' means both columns stay indexed.",
                         d["_path"], None, "Set sort_field to 'creation' unless business logic needs 'modified'.", FW)
        # Implicit ordering: get_all/get_list/get_value with limit 1 / [0] and no order_by
        rx_first = re.compile(r"(get_all|get_list|db\.get_all|db\.get_list|get_value)\(")
        for f in iter_files(self.pkg_dir, {".py"}):
            tree, src = parse(f)
            if not tree:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Call):
                    fn = ast.unparse(n.func) if hasattr(ast, "unparse") else ""
                    if not rx_first.search(fn + "("):
                        continue
                    kws = {k.arg for k in n.keywords}
                    if "order_by" in kws:
                        continue
                    single = any(k.arg in ("limit", "limit_page_length", "page_length") and isinstance(k.value, ast.Constant) and k.value.value == 1 for k in n.keywords)
                    is_value_with_filters = fn.endswith("get_value") and len(n.args) > 1 and isinstance(n.args[1], (ast.Dict, ast.List))
                    if single or is_value_with_filters:
                        self.add("SORT-IMPLICIT", "medium" if single else "low", "Default sort", "Picks 'first' row without order_by; implicit ordering changed from modified desc to creation desc.",
                                 f, n.lineno, "Add order_by='modified desc' (old behaviour) or the order you actually need.", FW, ast.get_source_segment(src, n) or "")

    # ---------- checks: hooks semantics ----------
    def check_hooks(self):
        h = self.hooks
        # commit inside doc_events handlers
        for dt, evs in expand(h.get("doc_events")):
            for ev, paths in (evs or {}).items():
                for p in ([paths] if isinstance(paths, str) else paths or []):
                    r = self.app_func(p)
                    if r and isinstance(r[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if "frappe.db.commit" in (ast.unparse(r[1]) if hasattr(ast, "unparse") else ""):
                            self.add("HOOK-COMMIT", "high", "Transactions", f"doc_events[{dt}][{ev}] -> {p} calls frappe.db.commit(); not supported from document hooks in v16.",
                                     r[0], r[1].lineno, "Remove the commit; move post-commit work to frappe.enqueue(..., enqueue_after_commit=True).", FW)
                    elif not r and p.startswith(self.app + "."):
                        self.add("HOOK-MISSING", "high", "Hooks", f"doc_events handler {p} not found in app.", self.hooks_path)
        # has_permission must return True explicitly
        for dt, p in expand(h.get("has_permission")):
            r = self.app_func(p)
            if r and isinstance(r[1], ast.FunctionDef):
                rets = [n for n in ast.walk(r[1]) if isinstance(n, ast.Return)]
                truthy = any(isinstance(x.value, ast.Constant) and x.value.value is True for x in rets)
                last = r[1].body[-1] if r[1].body else None
                falls_off = not isinstance(last, (ast.Return, ast.Raise))
                bare = any(x.value is None for x in rets)
                if falls_off or bare or not truthy:
                    self.add("PERM-HOOK-RETURN", "high", "Permissions", f"has_permission hook for '{dt}' ({p}) can return None. v16 treats anything but True as denied.",
                             r[0], r[1].lineno, "End with an explicit `return True` on the allow path.", "https://github.com/frappe/frappe/pull/24253")
        # override_doctype_class must inherit the overridden class
        for key in ("override_doctype_class", "extend_doctype_class"):
            for dt, paths in expand(h.get(key)):
                for p in ([paths] if isinstance(paths, str) else paths):
                    r = self.app_func(p)
                    if not r:
                        self.add("HOOK-MISSING", "high", "Hooks", f"{key}[{dt}] -> {p} not found in app.", self.hooks_path)
                        continue
                    f, cls = r
                    if key == "override_doctype_class" and isinstance(cls, ast.ClassDef):
                        if not cls.bases:
                            self.add("OVR-NO-BASE", "high", "Overrides", f"override_doctype_class['{dt}'] class {cls.name} has no base class. v16 requires it to extend the original controller.",
                                     f, cls.lineno, "class X(OriginalController): ... and call super() in overridden hooks.", "https://github.com/frappe/frappe/pull/26152")
                        elif self.tgt:
                            std = self._std_controller(self.tgt_all, dt)
                            if std and not self._inherits(f, cls, std):
                                self.add("OVR-NOT-SUBCLASS", "high", "Overrides", f"{cls.name} does not inherit from the v-target controller {std} for '{dt}'.",
                                         f, cls.lineno, f"Inherit from {std} (directly or via a subclass).", "https://github.com/frappe/frappe/pull/26152")
        # workspace / navigation
        if self.fixtures and any("workspace" in k for k in self.fixtures) or "Workspace" in json.dumps(h.get("fixtures", "")):
            self.add("NAV-WORKSPACE-FIXTURE", "high", "Routes & UI", "App exports Workspace fixtures. v16 regenerates standard workspaces; modifications to standard workspaces are lost.",
                     self.hooks_path, None, "Ship only app-owned workspaces; add Workspace Sidebar + Desktop Icon records.", FW)
        if list(self.pkg_dir.rglob("workspace/*/*.json")) and "add_to_apps_screen" not in h:
            self.add("NAV-APPS-SCREEN", "low", "Routes & UI", "App has workspaces but no add_to_apps_screen hook; v16 Desktop icon autogeneration relies on it.",
                     self.hooks_path, None, "Add add_to_apps_screen = [{name, logo, title, route}] and curate a Workspace Sidebar.", "https://docs.frappe.io/framework/user/en/apps-page")
        if "Number Card" in json.dumps(h.get("fixtures", "")) or "Dashboard Chart" in json.dumps(h.get("fixtures", "")):
            self.add("FIX-CARD-FILTERS", "medium", "Query builder", "Number Card / Dashboard Chart fixtures: verify filters_json has no 5th element and aggregate syntax is QB-compatible.",
                     self.hooks_path, None, "", QB)
        # Required apps that left core
        for k in ("doc_events", "doctype_js", "override_doctype_class"):
            for dt, _ in expand(h.get(k)):
                if re.search(r"Blog|Newsletter|Energy Point|Transaction Log", dt):
                    self.add("MOD-MOVED-HOOK", "high", "Removed modules", f"{k} targets '{dt}', which moved out of core in v16.", self.hooks_path, None, "Install the extracted app and add it to required_apps.", FW)

    def _std_controller(self, ts: TreeSet, dt: str):
        d = ts.doctype(dt)
        if not d:
            return None
        py = d["_path"].with_suffix(".py")
        cname = dt.replace(" ", "").replace("-", "")
        for t in ts.trees.values():
            for mod, f in t.modules.items():
                if f == py:
                    return f"{mod}.{cname}"
        return None

    def _inherits(self, f, cls, target_dotted, depth=0):
        if depth > 8:
            return False
        for b in cls.bases:
            bd = self._base_dotted(f, b) or (f"{self._dotted_of(f)}.{b.id}" if isinstance(b, ast.Name) else None)
            if not bd:
                continue
            if bd == target_dotted:
                return True
            r = self.tgt_all.resolve(bd)
            if r and r[0] == "def" and isinstance(r[2], ast.ClassDef):
                real = class_dotted(self.tgt_all, r[1], r[2].name)
                if real == target_dotted or self._inherits(r[1], r[2], target_dotted, depth + 1):
                    return True
        return False

    # ---------- checks: resolve against target / drift vs source ----------
    def check_symbols(self):
        if not self.tgt:
            return
        seen = set()
        for f, line, mod, name in self.core_imports:
            if not self.tgt.owns(mod):
                continue
            key = (mod, name)
            dotted = f"{mod}.{name}" if name and name != "*" else mod
            t = self.tgt.resolve(dotted) or (self.tgt.resolve(mod) if name == "*" else None)
            if t is None and name and self.tgt.module(dotted):
                t = ("module", self.tgt.module(dotted))
            s = self.src.resolve(dotted) if self.src else None
            if t is None:
                if self.src and s is None:
                    continue  # unresolvable in both (dynamic/C ext) - skip noise
                self.add("IMPORT-REMOVED", "high", "Core API", f"`{dotted}` does not exist in target {mod.split('.')[0]}.", f, line,
                         "Find the new location (git log -S in the target repo) or vendor the helper.", "", f"from {mod} import {name}" if name else f"import {mod}")
                continue
            if key in seen:
                continue
            seen.add(key)
            if s and s[0] == "def" and t[0] == "def" and isinstance(s[2], (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(t[2], (ast.FunctionDef, ast.AsyncFunctionDef)):
                if sig_shape(s[2]) != sig_shape(t[2]):
                    self.add("API-SIGNATURE", "medium", "Core API", f"Signature of `{dotted}` changed: ({sig(s[2])}) -> ({sig(t[2])}).", f, line,
                             "Check every call site in the app.", "")
                elif norm(s[2]) != norm(t[2]):
                    self.add("API-BEHAVIOUR", "info", "Core API", f"Body of `{dotted}` changed upstream (same signature).", f, line, "Skim the upstream diff if the app depends on its behaviour.", "")
        for f, line, dotted in self.dotted_refs:
            if not self.tgt.owns(dotted):
                continue
            t = self.tgt.resolve(dotted)
            s = self.src.resolve(dotted) if self.src else "n/a"
            if t is None and s is not None:
                self.add("METHOD-REF-REMOVED", "high", "Core API", f"Method path `{dotted}` (frappe.call/enqueue/get_attr) not found in target.", f, line,
                         "Point to the new method or reimplement in the app.", "")
        # override_whitelisted_methods originals
        for orig, new in expand(self.hooks.get("override_whitelisted_methods")):
            t = self.tgt.resolve(orig)
            if t is None:
                self.add("OWM-TARGET-GONE", "high", "Overrides", f"override_whitelisted_methods key `{orig}` no longer exists in target - override is dead or callers moved.", self.hooks_path, None,
                         "Locate the replacement endpoint and override that instead.", "")
            elif self.src:
                s = self.src.resolve(orig)
                if s and s[0] == t[0] == "def" and isinstance(s[2], ast.FunctionDef) and isinstance(t[2], ast.FunctionDef):
                    if sig_shape(s[2]) != sig_shape(t[2]):
                        self.add("OWM-SIGNATURE", "high", "Overrides", f"`{orig}` signature changed ({sig(s[2])}) -> ({sig(t[2])}); your override `{new}` must accept the new args.", self.hooks_path, None, "", "")
                    elif norm(s[2]) != norm(t[2]):
                        self.add("OWM-DRIFT", "medium", "Overrides", f"Upstream `{orig}` changed; re-port those changes into your override `{new}`.", self.hooks_path, None, "", "")
        # doctypes referenced by hooks exist in target
        own = set(self.app_tree.doctypes)
        for dt, why in self.touched.items():
            if dt in own:
                continue
            in_t = self.tgt.doctype(dt)
            in_s = self.src.doctype(dt) if self.src else None
            hard = why - {"code"}
            if not in_t and (in_s or (not self.src and hard)):
                self.add("DOCTYPE-REMOVED", "high", "Schema", f"Standard DocType '{dt}' ({', '.join(sorted(why))}) not found in target trees.", self.hooks_path if hard else None, None,
                         "Renamed/moved to another app? Search the target repos, add the app or rename references.", "")

    def check_overrides_drift(self):
        """For every app class extending a core class: overridden methods that changed or vanished upstream."""
        if not self.tgt:
            return
        for f, cls, base in self.subclasses:
            tr = self.tgt_all.resolve(base)
            sr = self.src_all.resolve(base) if self.src else None
            if tr is None:
                if not self.src or sr is not None:
                    self.add("BASE-REMOVED", "high", "Overrides", f"{cls.name} extends `{base}`, which is gone in target.", f, cls.lineno, "Re-base on the replacement class.", "")
                continue
            if not (tr[0] == "def" and isinstance(tr[2], ast.ClassDef)):
                continue
            for m in cls.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                tm = class_method(self.tgt_all, tr[1], tr[2], m.name)
                sm = class_method(self.src_all, sr[1], sr[2], m.name) if sr and sr[0] == "def" and isinstance(sr[2], ast.ClassDef) else None
                calls_super = "super()" in ast.unparse(m)
                if sm and not tm:
                    self.add("OVR-METHOD-GONE", "high", "Overrides", f"{cls.name}.{m.name} overrides {sm[2]}.{m.name}, which no longer exists in target. The override is never called by core now.",
                             f, m.lineno, "Find where the logic moved and re-hook.", "")
                elif sm and tm:
                    if sig_shape(sm[1]) != sig_shape(tm[1]):
                        self.add("OVR-SIGNATURE", "high", "Overrides", f"{cls.name}.{m.name}: upstream signature changed ({sig(sm[1])}) -> ({sig(tm[1])}).", f, m.lineno,
                                 "Update the override signature and super() call.", "")
                    elif norm(sm[1]) != norm(tm[1]):
                        sev = "low" if calls_super else "medium"
                        self.add("OVR-DRIFT", sev, "Overrides", f"{cls.name}.{m.name} overrides {tm[2]}.{m.name}, whose body changed upstream" + (" (you call super(), so you inherit the change - just re-test)." if calls_super else " and you do NOT call super(): upstream fixes are being skipped."),
                                 f, m.lineno, "Diff the upstream method between versions and port relevant changes." if not calls_super else "Re-test.", "")

    def check_schema(self):
        if not self.tgt:
            return
        cf_rows = self.fixtures.get("custom_field.json", [])
        ps_rows = self.fixtures.get("property_setter.json", [])
        custom_by_dt = defaultdict(set)
        for r in cf_rows:
            custom_by_dt[r.get("dt")].add(r.get("fieldname"))
        for r in cf_rows:
            dt, fn = r.get("dt"), r.get("fieldname")
            td = self.tgt.doctype(dt)
            if not td:
                continue
            tf = {x.get("fieldname") for x in td.get("fields", [])}
            sd = self.src.doctype(dt) if self.src else None
            sf = {x.get("fieldname") for x in sd.get("fields", [])} if sd else set()
            if fn in tf and fn not in sf:
                self.add("CF-COLLISION", "high", "Schema", f"Custom Field {dt}.{fn} collides with a NEW standard field of the same name in target.",
                         self.pkg_dir / "fixtures/custom_field.json", None, "Rename the custom field (patch to copy data) or drop it in favour of the standard field.", "")
            ia = r.get("insert_after")
            if ia and ia not in tf and ia not in custom_by_dt[dt]:
                self.add("CF-INSERT-AFTER", "low", "Schema", f"Custom Field {dt}.{fn} insert_after '{ia}' not present in target - field will be placed at the end.",
                         self.pkg_dir / "fixtures/custom_field.json", None, "Pick a new anchor field.", "")
        for r in ps_rows:
            dt, fn, prop = r.get("doc_type"), r.get("field_name"), r.get("property")
            td = self.tgt.doctype(dt)
            if not td or not fn:
                continue
            tf = {x.get("fieldname"): x for x in td.get("fields", [])}
            if fn not in tf and fn not in custom_by_dt[dt]:
                self.add("PS-FIELD-GONE", "medium", "Schema", f"Property Setter {dt}.{fn}.{prop} targets a field missing in target.",
                         self.pkg_dir / "fixtures/property_setter.json", None, "Drop or retarget the property setter.", "")
            elif fn in tf and prop in tf[fn] and str(tf[fn][prop]) == str(r.get("value")):
                self.add("PS-REDUNDANT", "info", "Schema", f"Property Setter {dt}.{fn}.{prop}={r.get('value')} now matches the standard value.",
                         self.pkg_dir / "fixtures/property_setter.json", None, "Can be removed.", "")
        if not self.src:
            return
        # field diff for touched doctypes + usage of removed fields in app code
        app_text = {f: f.read_text(encoding="utf-8", errors="replace") for f in iter_files(self.pkg_dir, {".py", ".js", ".html", ".jinja", ".json", ".vue"})}
        diffs = {}
        for dt in sorted(self.touched):
            sd, td = self.src.doctype(dt), self.tgt.doctype(dt)
            if not (sd and td):
                continue
            LAYOUT = {"Section Break", "Column Break", "Tab Break", "HTML", "Fold", "Heading", "Button"}
            sf = {x["fieldname"]: x for x in sd.get("fields", []) if x.get("fieldname") and x.get("fieldtype") not in LAYOUT}
            tf = {x["fieldname"]: x for x in td.get("fields", []) if x.get("fieldname") and x.get("fieldtype") not in LAYOUT}
            removed = sorted(set(sf) - set(tf))
            added = sorted(set(tf) - set(sf))
            changed = sorted(fn for fn in set(sf) & set(tf) if (sf[fn].get("fieldtype"), sf[fn].get("options")) != (tf[fn].get("fieldtype"), tf[fn].get("options")))
            new_reqd = sorted(fn for fn in tf if tf[fn].get("reqd") and not sf.get(fn, {}).get("reqd") and not tf[fn].get("default"))
            if removed or changed or new_reqd or added:
                diffs[dt] = dict(removed=removed, added=added, type_or_options_changed=changed, newly_mandatory=new_reqd)
            for fn in removed:
                rx = re.compile(rf"""(\.{re.escape(fn)}\b|["']{re.escape(fn)}["'])""")
                dt_rx = re.compile(rf"""["']{re.escape(dt)}["']""")
                hits = [(f, i, l) for f, t in app_text.items() for i, l in enumerate(t.splitlines(), 1) if rx.search(l)]
                if not hits:
                    continue
                strong = [h for h in hits if dt_rx.search(app_text[h[0]])]
                sev = "high" if strong else "low"
                top = (strong or hits)[:8]
                locs = ", ".join(f"{Path(f).relative_to(self.root)}:{i}" for f, i, _ in top)
                self.add("FIELD-REMOVED-USED", sev, "Schema",
                         f"{dt}.{fn} was removed in target; app references '{fn}' {len(hits)}x"
                         + (f" ({len(strong)} in files that also reference '{dt}')" if strong else " (no file also names the DocType - likely a different context)")
                         + f". e.g. {locs}", top[0][0], top[0][1],
                         "Confirm the context; migrate to the replacement field or a custom field + data patch.", "", top[0][2])
            for fn in changed:
                a, b = sf[fn], tf[fn]
                if a.get("fieldtype") == b.get("fieldtype") == "Select":
                    ao = set((a.get("options") or "").split("\n")) - {""}
                    bo = set((b.get("options") or "").split("\n")) - {""}
                    desc = f"Select options changed (+{sorted(bo - ao)} -{sorted(ao - bo)})"
                else:
                    desc = f"{a.get('fieldtype')}({a.get('options') or ''}) -> {b.get('fieldtype')}({b.get('options') or ''})"
                rx = re.compile(rf"""(\.{re.escape(fn)}\b|["']{re.escape(fn)}["'])""")
                used = next(((f, i) for f, t in app_text.items() for i, l in enumerate(t.splitlines(), 1) if rx.search(l)), None)
                self.add("FIELD-TYPE-CHANGED", "medium" if used else "info", "Schema", f"{dt}.{fn}: {desc}." + (" Referenced by the app." if used else ""),
                         used[0] if used else None, used[1] if used else None,
                         "Check app code, custom fields depending on it, reports and integrations.", "")
            if new_reqd and ("code" in self.touched[dt] or "doc_events" in self.touched[dt]):
                self.add("FIELD-NEW-MANDATORY", "medium", "Schema", f"{dt}: newly mandatory fields {new_reqd}. App code that creates {dt} may fail validation.", None, None,
                         "Set these fields when inserting docs programmatically / in integrations.", "")
        self.inventory["schema_diff"] = diffs

    def run(self):
        self.build_inventory()
        self.run_rules()
        self.check_hooks()
        self.check_symbols()
        self.check_overrides_drift()
        self.check_schema()
        # findings in test code: downgrade one level and tag
        down = {"high": "medium", "medium": "low", "low": "low", "info": "info"}
        for f in self.findings:
            if re.search(r"(^|/)(tests?/|test_[^/]*\.py$)", f["file"] or ""):
                f["severity"] = down[f["severity"]]
                f["message"] = "[test code] " + f["message"]
        # dedupe
        uniq, seen = [], set()
        for f in self.findings:
            k = (f["id"], f["file"], f["line"], f["message"])
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        self.findings = sorted(uniq, key=lambda x: (SEV_ORDER[x["severity"]], x["category"], x["id"], x["file"], x["line"] or 0))
        return self


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------

def md_report(sc: Scanner, args) -> str:
    F, inv = sc.findings, sc.inventory
    cnt = defaultdict(int)
    for f in F:
        cnt[f["severity"]] += 1
    out = [f"# Upgrade impact - `{inv['app']}`  ({args.rules})", ""]
    out.append(f"Source trees: {', '.join(f'{k}={v.root}' for k, v in sc.src.trees.items()) or '(none - drift checks skipped)'}  ")
    out.append(f"Target trees: {', '.join(f'{k}={v.root}' for k, v in sc.tgt.trees.items()) or '(none - rule pack only)'}")
    out.append("")
    if PARSE_DEGRADED:
        out.append(f"> Note: {len(PARSE_DEGRADED)} file(s) use syntax newer than this Python ({sys.version.split()[0]}); offending lines were skipped. Re-run with the target's Python (v16: 3.14) for full fidelity.")
        out.append("")
    out.append(f"**High {cnt['high']} · Medium {cnt['medium']} · Low {cnt['low']} · Info {cnt['info']}**")
    out.append("")
    out.append("## What the app changes in core")
    for k in ("doc_events", "override_doctype_class", "extend_doctype_class", "override_whitelisted_methods", "doctype_js",
              "has_permission", "permission_query_conditions", "scheduler_events", "fixtures", "required_apps"):
        if k in inv:
            v = inv[k]
            n = len(v) if isinstance(v, (dict, list)) else 1
            out.append(f"- **{k}**: {n} entr{'y' if n == 1 else 'ies'}" + (f" - {', '.join(str(x) for x in list(v)[:12])}" if isinstance(v, dict) else ""))
    out.append(f"- **core subclasses**: {len(inv['core_subclasses'])}")
    out.append(f"- **core imports**: {len(inv['core_imports'])} modules · **method refs**: {len(inv['core_method_refs'])}")
    out.append(f"- **patches**: {len(inv['patches'])} · **fixtures on disk**: {inv['fixture_files']}")
    out.append(f"- **standard DocTypes touched**: {len(inv['touched_standard_doctypes'])}")
    if inv.get("schema_diff"):
        out.append("")
        out.append("### Schema drift on touched DocTypes")
        out.append("| DocType | removed | newly mandatory | type/options changed | added |")
        out.append("|---|---|---|---|---|")
        for dt, d in inv["schema_diff"].items():
            out.append(f"| {dt} | {', '.join(d['removed']) or '-'} | {', '.join(d['newly_mandatory']) or '-'} | {', '.join(d['type_or_options_changed']) or '-'} | {len(d['added'])} |")
    for sev in ("high", "medium", "low", "info"):
        rows = [f for f in F if f["severity"] == sev]
        if not rows:
            continue
        out.append("")
        out.append(f"## {sev.upper()} ({len(rows)})")
        groups = defaultdict(list)
        for r in rows:
            groups[(r["category"], r["id"])].append(r)
        for (cat, rid), items in groups.items():
            out.append(f"### {cat} · {rid} ({len(items)})")
            out.append(items[0]["message"] if len({i['message'] for i in items}) == 1 else "")
            if items[0]["fix"]:
                out.append(f"*Fix:* {items[0]['fix']}")
            if items[0]["ref"]:
                out.append(f"*Ref:* {items[0]['ref']}")
            for i in items[:60]:
                loc = f"`{i['file']}:{i['line']}`" if i["line"] else (f"`{i['file']}`" if i["file"] else "")
                msg = "" if len({x['message'] for x in items}) == 1 else i["message"]
                snip = f" - `{i['snippet']}`" if i["snippet"] else ""
                out.append(f"- {loc} {msg}{snip}".rstrip())
            if len(items) > 60:
                out.append(f"- ... {len(items) - 60} more (see findings.json)")
            out.append("")
    return "\n".join(out)


def _jsonable(o):
    if isinstance(o, dict):
        return {(", ".join(k) if isinstance(k, tuple) else str(k)): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(x) for x in o]
    return o


def parse_pairs(pairs):
    out = []
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"[error] expected name=path, got {p}")
        n, path = p.split("=", 1)
        out.append(Tree(n.strip(), path.strip()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True, help="custom app repo root (folder containing <app>/hooks.py)")
    ap.add_argument("--source", action="append", help="name=path of a SOURCE-version core repo (repeatable)")
    ap.add_argument("--target", action="append", help="name=path of a TARGET-version core repo (repeatable)")
    ap.add_argument("--rules", default="v15-v16", help=f"rule pack: {', '.join(RULE_PACKS)} or 'none'")
    ap.add_argument("--out", default="upgrade_report")
    a = ap.parse_args()
    rules = RULE_PACKS.get(a.rules, []) if a.rules != "none" else []
    sc = Scanner(Path(a.app).resolve(), TreeSet(parse_pairs(a.source)), TreeSet(parse_pairs(a.target)), rules).run()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "findings.json").write_text(json.dumps(sc.findings, indent=1, default=str))
    inv = sc.inventory
    (out / "inventory.json").write_text(json.dumps(_jsonable(inv), indent=1, default=str))
    (out / "report.md").write_text(md_report(sc, a))
    c = defaultdict(int)
    for f in sc.findings:
        c[f["severity"]] += 1
    print(f"{sc.app}: high={c['high']} medium={c['medium']} low={c['low']} info={c['info']} -> {out}/report.md")


if __name__ == "__main__":
    main()
