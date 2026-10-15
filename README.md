# Bench Wizard

A local web wizard that sets up a Frappe bench (version 15 or 16) on Ubuntu or macOS: it checks
prerequisites, installs what is missing, then runs `bench init`, `get-app`, `new-site`
and `install-app` for the apps you choose, optionally restoring a database backup.

Python 3.10+ standard library only. No build step, no dependencies.

## Run

```bash
./start.sh                                # or: python3 -m bench_wizard
python3 -m bench_wizard --install-desktop # adds an app-menu icon (on a Mac: ~/Applications/Bench Wizard.app)
```

It opens `http://127.0.0.1:<port>/` in your browser (local only, token protected).

## What it does

- **System check:** Python, Node (nvm), MariaDB, Redis, wkhtmltopdf and the rest; installs missing ones with
  `apt` and sudo on Ubuntu, or Homebrew on a Mac.
- **Apps:** pick from `catalog.json`, paste a JSON list of `{url, branch}` (branches may be tags),
  or add a custom repo. Dependencies are read from each app's `pyproject.toml` and `hooks.py`
  and installed first. Private HTTPS URLs may carry a token; it is passed to git through the
  environment and scrubbed from logs.
- **Site data:** a new empty site, or restore from a `.sql`/`.sql.gz` backup (full or partial)
  with optional public/private file archives. Apps in the backup that you did not select are
  removed from the site's installed apps before migrate.
- **Review:** the exact commands, plus pre-flight checks (paths, MariaDB, disk, `git ls-remote`).
- **Run:** live log, retry from the failed step, cancel.

The wizard always creates a new bench folder; it does not modify existing benches.

## Catalog

`catalog.json` lists Frappe versions, apps and presets. Add your own apps in
`~/.config/bench-wizard/catalog.json`; it is merged on top.

## Tests

```bash
python3 -m unittest discover -s tests
```

## macOS

Install [Homebrew](https://brew.sh) first; the wizard installs everything else through it, as your user.
Homebrew no longer packages wkhtmltopdf, so the wizard installs the upstream `.pkg` with sudo; it is an Intel
build and runs under Rosetta on Apple silicon. MariaDB runs as a Homebrew service, and the charset settings go to
`$(brew --prefix)/etc/my.cnf.d/99-frappe.cnf`. The app icon is `bench_wizard/web/icon.icns`, built from
`icon-mac.svg` (render it at 1024px, make an `.iconset` with `sips`, then run `iconutil -c icns`). In `--app` mode the wizard keeps running until you quit the
browser window with ⌘Q, since closing a window does not quit a Mac app.

## Notes

- Bench folder paths must not contain spaces.
- Passwords and tokens are never written to disk or shown in logs.
