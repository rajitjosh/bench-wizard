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

## Copy apps from a Frappe Cloud site

`extension/` is a small browser extension (Chrome, Edge, Brave) that lists the apps on one of your
Frappe Cloud sites as `[{"app", "url", "branch", "commit"}]` and sends them to the wizard.

1. The wizard's Apps step has an "Import from Frappe Cloud" card. Until the extension is installed it shows
   the steps: open `chrome://extensions`, turn on Developer mode, click "Load unpacked" and pick the
   `extension` folder ("Show folder" opens it). Browsers do not let a page install an extension itself.
2. Log in at `cloud.frappe.io/dashboard` (or `frappecloud.com/dashboard`) (open a site to preselect it), then click the extension's icon.
3. Click "Send to Bench Wizard". The list lands in "Paste an app list" for you to check before adding.
   Copy and Download work too.

It calls the dashboard's own API (`press.api.site.all`, `press.api.site.installed_apps`) from inside the
dashboard tab with your existing login; it stores nothing and needs no API key. These are internal
dashboard endpoints, so a Frappe Cloud change may break it. Private repos still need your own Git token
in the wizard. A site may run an older commit than its branch head; the wizard clones the branch.

The wizard listens on the first free port of 47615–47619 so the extension can find it (with `--port`,
it cannot). The extension checks in on install, at browser start and once a minute, which is how the card
shows "connected". The two calls it makes need no token but are refused unless they come from an
extension (`Origin: chrome-extension://…`), which web pages cannot fake.

## Which GitHub account it uses

With several GitHub accounts as SSH host aliases in `~/.ssh/config`, pick the one the wizard uses in
`~/.config/bench-wizard/settings.json`:

```json
{"github_ssh_host": "github-work"}
```

Every GitHub URL the wizard fetches (HTTPS or SSH, for `bench init`, `get-app` and the checks) then goes over
SSH through that alias and its key. It is passed to git through the environment of the wizard's own commands
only; your git config and other projects are unchanged. A token inside a URL still wins for its owner. The
bench's own remotes keep their original URLs, so a later `bench update` uses your normal git setup.

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

The window belongs to a browser process of its own, so in the Dock it shows as a second browser icon (and
a minimised window goes into that icon when "Minimise windows into application icon" is on). Opening Bench
Wizard again, from Spotlight or the Applications folder, brings that window back, un-minimising it; only
if it was closed does a new one open. The wizard drives its window over the browser's DevTools protocol
on a private pipe (`--remote-debugging-pipe`), not a network port.

On Linux the launcher's `StartupWMClass` lets the desktop focus the open window itself. A snap browser
(Ubuntu's Chromium) keeps its window profile in `~/snap/<name>/common/`, since snaps cannot use hidden
folders in your home, and runs without the DevTools pipe, so reopening there opens a new window.

## Notes

- Bench folder paths must not contain spaces.
- Passwords and tokens are never written to disk or shown in logs.

## Future plans

**Several setups at once.** Today every window talks to one wizard, which holds one plan and one run: a second
window shows the setup already running, and starting another is refused. To set up two benches side by side:

1. **Unique Redis ports per bench.** `bench init` picks ports from sibling bench folders, so two benches made at
   the same time, or in different parent folders, can both get 11000/13000 and clash. The wizard should pick free
   ports itself and write them to the bench's config right after `bench init`. Worth doing even for one setup at
   a time.
2. **One System check install at a time.** Homebrew and apt hold locks, so installs from different setups
   should queue.
3. **Sessions.** Each window gets its own plan, run, log stream and Done screen; opening the app again starts a
   new session, and a "running setups" list reopens one whose window was closed. Bench folders and site names
   are checked against other active sessions.

Concurrent `pip` and `yarn` runs share caches and can occasionally fail; retrying the step is the fallback.
