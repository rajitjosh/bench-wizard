---
name: frappe-upgrade-impact
description: Evaluate a Frappe/ERPNext custom app against two framework versions (e.g. v15 to v16) and report every change the upgrade requires, with effort estimate and fixes.
---

# Frappe / ERPNext custom-app upgrade impact

Use this when someone asks what it takes to move a custom Frappe app (or a client bench) from one major version to another — "will our app work on v16", "upgrade assessment", "what breaks moving ICP to v16", "estimate the v15→v16 migration". The output is an evidence-backed impact report: what the app changes in core, every place that breaks or drifts on the target version, the fix for each, and an effort estimate.

The scanner (`scripts/frappe_upgrade_scan.py`) does the mechanical work (≈1s per app). Your job is to feed it the right sources, then **triage** its findings by reading the code — it is a lead generator, not the verdict.

## Inputs to settle first

1. **The custom app source** — a GitHub repo (clone it), a folder on the person's linked computer, or an uploaded zip. Need the repo root that contains `<app>/hooks.py`.
2. **Source and target versions** — default `version-15` → `version-16`. If the client runs a specific minor (`bench version` output, e.g. `erpnext 15.42.1`), use that tag (`v15.42.1`) as the source for sharper drift detection.
3. **Other apps on the bench** the custom app imports from (read `required_apps` in hooks.py and the app's `from X import` lines): commonly `erpnext`, `hrms`, `payments`, `india_compliance`, `lending`. Each needs a source and target tree.
4. **Optional: site access** (bench console / DB dump / MCP connection to the site). Lets you check DB-stored customisations the app repo doesn't contain (step 5).

Only ask the person when the app source or target version genuinely can't be inferred.

## Workflow

### 1. Fetch the trees (shallow clones are enough)

```bash
W=./upgrade_work && mkdir -p $W/trees && cd $W/trees
for v in 15 16; do
  git clone -q --depth 1 -b version-$v https://github.com/frappe/frappe  frappe-v$v &
  git clone -q --depth 1 -b version-$v https://github.com/frappe/erpnext erpnext-v$v &
done; wait
# add hrms / payments / india_compliance the same way when the app imports them
# india_compliance lives at https://github.com/resilient-tech/india-compliance
git -C frappe-v16 rev-parse --short HEAD   # record commit hashes for the report
```

### 2. Run the scanner

The scanner is `scripts/frappe_upgrade_scan.py`, next to this file (in Bench Wizard it is `bench_wizard/upgrade_scan.py`). Run it **with the target's Python** (v16 code uses Python 3.14 syntax; older interpreters still work but skip a few lines and the report says so):

```bash
pip install -q uv --break-system-packages 2>/dev/null
uv run -q --python 3.14 --no-project python scripts/frappe_upgrade_scan.py \
  --app /path/to/custom_app \
  --source frappe=$W/trees/frappe-v15 --source erpnext=$W/trees/erpnext-v15 \
  --target frappe=$W/trees/frappe-v16 --target erpnext=$W/trees/erpnext-v16 \
  --rules v15-v16 --out $W/report
```

Outputs: `report.md` (grouped by severity), `findings.json`, `inventory.json`.

What it checks:

| Layer | Checks |
|---|---|
| Inventory | hooks (doc_events, override_doctype_class, extend_doctype_class, override_whitelisted_methods, doctype_js, has_permission, permission_query_conditions, scheduler, fixtures…), core imports, `frappe.call`/`enqueue` method paths, subclassed core classes, fixtures, patches, standard DocTypes touched |
| Core API resolution | every core import and dotted method path resolved in the target (follows re-exports and `import *`); removed symbols; parameter-shape changes; upstream body changes |
| Override drift | for every app class extending a core controller: overridden methods that vanished, changed signature, or changed body upstream (flags harder when you don't call `super()`); `override_whitelisted_methods` targets gone/changed; override classes that don't inherit the original (v16 hard requirement) |
| Schema | for every touched standard DocType: removed fields still referenced by the app, type/option changes, newly mandatory fields, custom-field name collisions with new standard fields, dead `insert_after` anchors, property setters on vanished fields |
| Rule pack `v15-v16` | query-builder syntax, single-doctype set_value, typed get_value, commit in doc hooks, has_permission return, /app→/desk, moved modules, translations, POST-only endpoints, tests API, Python 3.14 stdlib removals, packaging pins, Vue 2, ERPNext item-wise tax child table, TDS refactor, removed whitelisted helpers |

Findings in test files are auto-downgraded one level and tagged `[test code]`.

### 3. Triage — mandatory

For **every high and medium finding**, open the code and decide: confirmed / false positive / needs runtime test. Notes:

- `QB-FUNC`, `QB-DISTINCT`, `QB-LITERAL` are false positives when the string feeds `frappe.db.sql()` — only `get_all/get_list/get_value/qb.get_query` (and Report Builder / Number Card JSON) are affected.
- `FIELD-REMOVED-USED` is high only when the hit file also names the DocType; still confirm the attribute is on that DocType. Check the target repo's patches to find the replacement field: `grep -rn "<fieldname>" $W/trees/erpnext-v16/erpnext/patches* | head`.
- `OVR-DRIFT` / `OWM-DRIFT` / `API-SIGNATURE`: show the actual upstream change. Extract both versions of the function and diff them:

```bash
python3 - <<'EOF'
import ast,sys,difflib
def src(path, qual):
    t=open(path).read(); m=ast.parse(t); node=m
    for part in qual.split('.'):
        node=next(n for n in ast.walk(node) if isinstance(n,(ast.FunctionDef,ast.ClassDef,ast.AsyncFunctionDef)) and n.name==part)
    return ast.get_source_segment(t,node).splitlines()
a=src(sys.argv[1],sys.argv[3]); b=src(sys.argv[2],sys.argv[3])
print("\n".join(difflib.unified_diff(a,b,"source","target",lineterm="")))
EOF
# args: <v15 file> <v16 file> <Class.method or func>
```

  Run it under `uv run --python 3.14` when the v16 file uses 3.14 syntax.
- `SORT-IMPLICIT`: real only if the code depends on which row comes first (e.g. "latest rate", "last entry").
- `DB-COMMIT`: high when reachable from a doc hook (`HOOK-COMMIT` already proves that path); in whitelisted methods/background jobs it's fine.
- Drop `info` items from the report unless they back up a confirmed finding.

### 4. Things the scanner can't see — check by hand

- **Client JS behaviour**: report/page/dashboard JS now runs as an IIFE — top-level `var`/`function` in `report/*/*.js` or `page/*/*.js` that other files rely on as globals must move to `window.`; custom list views relying on the removed right sidebar; awesome bar / navbar DOM tweaks.
- **Workspaces & navigation**: modified *standard* workspaces are reset; app workspaces need a curated Workspace Sidebar + Desktop Icon; `add_to_apps_screen` hook.
- **Frontend build**: Node 24, Vue 3 only (frappe-ui apps), `package.json` engines, any `frappe.require` of removed bundles.
- **Print formats / Jinja** that read `item_wise_tax_detail` or build `/app/...` links.
- **Integrations** (Shopify, ShipStation, payment gateways…) that write tax rows: must set `dont_recompute_tax=1` and populate `doc._item_wise_tax_details`.
- **Deployment**: Python 3.14 + Node 24 on the server, regenerated Procfile/supervisor config, Google Drive/S3/Dropbox backups now need `frappe/offsite_backups`; Blog/Newsletter/Energy Points need their extracted apps.
- **Upgrade path**: v14 sites go v14 → v15 → v16; never jump. `bench migrate` on a restored copy first.

### 5. Site-level customisations (when site access exists)

DB-stored code isn't in the app repo but breaks the same way. Via `bench --site X console` or the site's API, pull and run the same rules over:

```python
for dt, fields in {
  "Server Script": ["script"], "Client Script": ["script"], "Report": ["query", "report_script", "javascript"],
  "Print Format": ["html"], "Notification": ["message", "condition"], "Number Card": ["filters_json", "aggregate_function_based_on"],
  "Dashboard Chart": ["filters_json"], "Custom Field": ["dt", "fieldname", "insert_after"], "Property Setter": ["doc_type", "field_name", "property"],
}.items():
    rows = frappe.get_all(dt, fields=["name", *fields], filters={"disabled": 0} if frappe.get_meta(dt).has_field("disabled") else {})
    # dump to JSON, then grep for: item_wise_tax_detail, /app/, sum(…) in fields, ,false]] in filters_json, db.commit, set_value(…None…)
```

Export with `bench --site X export-fixtures` alternatively, drop the JSON into a scratch folder shaped like an app (`site_dump/site_dump/hooks.py` with `app_name="site_dump"` plus the JSON files) and run the scanner on it.

### 6. Deliverable

Write the report for a reader who has to plan and approve the work (client PM, tech lead). Structure:

1. **Verdict & effort** — upgrade-readiness in one line; total estimate in person-days with a range.
2. **What the app customises in core** — from inventory (counts + the important ones).
3. **Required changes** — one row per confirmed issue group: area, files, what breaks, fix (before/after snippet for the non-obvious ones), effort (S ≤ 1h, M ≤ ½ day, L ≤ 2 days, XL needs design), risk.
4. **Behaviour changes to re-test** — sort order, typed single values, permission hooks, overrides that skip `super()`, newly mandatory fields.
5. **Infra & runbook** — versions, extracted apps to install, migration order, rollback point.
6. **Test plan** — the business flows touched by the overrides and doc_events (e.g. "Sales Invoice submit with GST", "Shopify order sync").
7. **Appendix** — scanner version, source/target commit hashes, false positives dismissed and why.

Effort heuristic: mechanical rule hits (set_single_value, /desk links, distinct, `in_test`, test base classes, pins) are S each and batchable; query-builder rewrites S–M each; override drift M per method without `super()`; removed core APIs M–L; item-wise tax / TDS integrations L–XL; Vue 2 → 3 XL.

When the person asks for it, apply the mechanical fixes on a branch (`upgrade/v16`) in their repo, compile/lint (`python -m compileall -q`, `ruff check` if configured), and rerun the scanner to show the before/after counts. Don't push or open PRs unless asked.

## Other version pairs

The structural layers (imports, overrides, schema) are version-agnostic — just point `--source/--target` at the right branches. The regex rule pack only exists for `v15-v16`; for another hop (e.g. v16 → v17, v14 → v15) run with `--rules none`, then read `https://github.com/frappe/frappe/wiki/Migrating-to-version-<N>` and `https://github.com/frappe/erpnext/wiki/Migration-Guide-To-ERPNext-Version-<N>`, grep the app for each change, and — if the person wants it reusable — add a `RULES_V16_V17` list to the script following the existing rule shape.

## v15 → v16 breaking changes (reference)

Framework (`frappe/wiki/Migrating-to-version-16`, `query-builder-migration`):
- Python 3.14+, Node 24+. App pins (`frappe>=15,<16`, `requires-python`) must move.
- `get_all/get_list/get_value/qb.get_query` run on the query builder: no raw SQL functions in fields/filters/order_by (`{"SUM": "x", "as": "y"}` or pypika functions), no `"distinct x"` (use `distinct=True`), literals via `ValueWrapper`, `IfNull(Field("x"), 0)` in filters, `run=False` returns a query object, JSON filters max 4 elements.
- Implicit sort is `creation desc` (was `modified desc`) for list views and those APIs; doctype `sort_field` default changed.
- `frappe.db.commit()` not allowed from doc_events handlers; `frappe.sendmail(now=True)` no longer commits.
- `has_permission` hooks must return `True` explicitly; `has_permission(raise_exception=)` removed.
- `override_doctype_class` class must inherit the overridden class.
- `db.set_value(Single, None, …)` → `db.set_single_value`; `db.get_value` on singles returns typed values; `db.value_cache` nested; `db.count(cache=True)` per-transaction.
- `get_doc(dt, name, field=value)` no longer updates fields; `meta.get_valid_columns()` excludes virtual fields; `!= None` filters now `IS NOT NULL`; Time fields default to now only with "Now".
- Desk route `/app` → `/desk`; `/apps` deprecated; persistent Workspace Sidebar + Desktop screen; standard workspace edits lost.
- Moved out of core: Energy Points (`frappe/eps`), Newsletter, Blog, backup integrations (`frappe/offsite_backups`); Transaction Log and GeoIP removed.
- Translations: `get_translated_dict` hook and `get_lang_dict` / `translate.get_dict` / `get_lang_js` / `get_dict_from_hooks` removed.
- `logout`, `web_logout`, `upload_file`, `send_login_link` POST-only. Report/Page/Dashboard Chart JS evaluated as IIFE. `sanitize_html` uses nh3 (bleach no longer bundled). Site config cached ~1 min. `@site_cache` needs hashable args. System Console Administrator-only. Country needs ISO code. `bench version` output format changed.
- Tests: `FrappeTestCase` → `IntegrationTestCase`/`UnitTestCase` from `frappe.tests`; `test_dependencies` → `EXTRA_TEST_RECORD_DEPENDENCIES`; `frappe.flags.in_test` → `frappe.in_test`.
- New: `frappe.get_lazy_doc()` (lazy child tables) — an optimisation, not required.

ERPNext (`erpnext/wiki/Migration-Guide-To-ERPNext-Version-16`):
- `item_wise_tax_detail` JSON → `Item Wise Tax Detail` child table; tax writers set `dont_recompute_tax=1` and fill `doc._item_wise_tax_details` with row objects; reports query the child table.
- Tax Withholding refactored around `Tax Withholding Entry` (fields like `apply_tds`, `tax_withholding_category` moved/removed on several transactions).
- Removed: `bank_account.make_bank_account`, `pricing_rule.make_pricing_rule`, `erpnext.utils.make_pricing_rule`; Transaction Log (DE/FR).
- Sales Invoice via API no longer auto-fetches timesheets — call `add_timesheet_data`.
- Many `get_item_details` helpers now take an `ItemDetailsCtx` instead of `args`.
