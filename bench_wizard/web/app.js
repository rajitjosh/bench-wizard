'use strict';

const STEPS = [['project', 'Project'], ['check', 'System check'], ['apps', 'Apps'], ['review', 'Review'],
  ['run', 'Run'], ['done', 'Done']];
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const S = {
  step: 'project', reached: 0, catalog: null, errors: {},
  version: '', preset: '', folder: '', site: '', db: '', pw: '', folderEdited: false, siteEdited: false,
  siteData: 'new',
  mode: 'new', benchFolder: '', bench: null, benchSite: '', benchNew: false, benchBusy: false, benchMsg: '',
  restore: { sql: '', public: '', private: '', backup_key: '', site_key: '', mute_emails: true, pause_scheduler: true, sanitize: true },
  backup: null, backupMsg: '', backupPath: '',
  checks: null, mac: false, checksVersion: '', checking: false, sudo: '', installing: '', installLogs: {}, installMsg: '',
  toolsInstalled: false, rootPw: '', rootOk: null, rootMsg: '', rootBusy: false,
  apps: {}, appsKey: '', custom: { repo: '', branch: '' }, customMsg: '', customBusy: false, depWarn: null,
  importText: '', importBusy: false, importMsg: '', importErrors: [], ext: null,
  plan: null, planning: false, planMsg: '',
  run: null, activeStep: '', follow: true, logs: {}, cleanup: true, runMsg: '', showPw: false,
  runs: [], pwFor: {}, benchMigrate: false, migrateSites: {},
  proj: null, projFilter: 'all', projBusy: '', projMsg: '', projNote: '', projSite: {}, portEdits: {}, addFolder: '',
  mpApps: null, mpQuery: '', mpBusy: false, mpMsg: '', mp: null, mpBranch: '', migrateBackup: true, bp: null, fx: null,
};

// Every answer as the page first had it, for "Start over"; what is not an answer (checks, runs, projects) is kept.
const FRESH = JSON.stringify(S);
const KEEP = ['catalog', 'checks', 'mac', 'checksVersion', 'toolsInstalled', 'ext', 'run', 'runs', 'pwFor', 'follow', 'logs',
  'activeStep', 'cleanup', 'proj', 'projFilter', 'projSite', 'portEdits', 'addFolder', 'mpApps', 'bp', 'fx'];

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tilde = (p) => (S.catalog && (p === S.catalog.home || p.startsWith(S.catalog.home + '/')) ? '~' + p.slice(S.catalog.home.length) : p);
const profile = () => S.catalog.frappe_versions[S.version];
// Material icons (Apache 2.0), filled, on the 24px grid; one style everywhere.
const PATHS = {
  add: 'M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z',
  add_photo_alternate: 'M19 7v2.99s-1.99.01-2 0V7h-3s.01-1.99 0-2h3V2h2v3h3v2h-3zm-3 4V8h-3V5H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2v-8h-3zM5 19l3-4 2 3 3-4 4 5H5z',
  apps: 'M4 8h4V4H4v4zm6 12h4v-4h-4v4zm-6 0h4v-4H4v4zm0-6h4v-4H4v4zm6 0h4v-4h-4v4zm6-10v4h4V4h-4zm-6 4h4V4h-4v4zm6 6h4v-4h-4v4zm0 6h4v-4h-4v4z',
  arrow_back: 'M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z',
  arrow_forward: 'M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z',
  arrow_upward: 'M4 12l1.41 1.41L11 7.83V20h2V7.83l5.58 5.59L20 12l-8-8-8 8z',
  auto_awesome: 'M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zm-7.5.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z',
  build: 'M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z',
  check: 'M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z',
  close: 'M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z',
  restart_alt: 'M12 5V2L8 6l4 4V7c3.31 0 6 2.69 6 6 0 2.97-2.17 5.43-5 5.91v2.02c3.95-.49 7-3.85 7-7.93 0-4.42-3.58-8-8-8zm-6 8c0-1.65.67-3.15 1.76-4.24L6.34 7.34C4.9 8.79 4 10.79 4 13c0 4.08 3.05 7.44 7 7.93v-2.02c-2.83-.48-5-2.94-5-5.91z',
  contrast: 'M12 22c5.52 0 10-4.48 10-10S17.52 2 12 2 2 6.48 2 12s4.48 10 10 10zm1-17.93c3.94.49 7 3.86 7 7.93s-3.06 7.44-7 7.93V4.07z',
  create_new_folder: 'M20 6h-8l-2-2H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z',
  folder: 'M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z',
  dark_mode: 'M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-.98 1.37-2.58 2.26-4.4 2.26-2.98 0-5.4-2.42-5.4-5.4 0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z',
  dns: 'M20 13H4c-.55 0-1 .45-1 1v6c0 .55.45 1 1 1h16c.55 0 1-.45 1-1v-6c0-.55-.45-1-1-1zM7 19c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zM20 3H4c-.55 0-1 .45-1 1v6c0 .55.45 1 1 1h16c.55 0 1-.45 1-1V4c0-.55-.45-1-1-1zM7 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z',
  info: 'M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z',
  light_mode: 'M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zM2 13h2c.55 0 1-.45 1-1s-.45-1-1-1H2c-.55 0-1 .45-1 1s.45 1 1 1zm18 0h2c.55 0 1-.45 1-1s-.45-1-1-1h-2c-.55 0-1 .45-1 1s.45 1 1 1zM11 2v2c0 .55.45 1 1 1s1-.45 1-1V2c0-.55-.45-1-1-1s-1 .45-1 1zm0 18v2c0 .55.45 1 1 1s1-.45 1-1v-2c0-.55-.45-1-1-1s-1 .45-1 1zM5.99 4.58c-.39-.39-1.03-.39-1.41 0-.39.39-.39 1.03 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0s.39-1.03 0-1.41L5.99 4.58zm12.37 12.37c-.39-.39-1.03-.39-1.41 0-.39.39-.39 1.03 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0 .39-.39.39-1.03 0-1.41l-1.06-1.06zm1.06-10.96c.39-.39.39-1.03 0-1.41-.39-.39-1.03-.39-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06zM7.05 18.36c.39-.39.39-1.03 0-1.41-.39-.39-1.03-.39-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06z',
  memory: 'M15 9H9v6h6V9zm-2 4h-2v-2h2v2zm8-2V9h-2V7c0-1.1-.9-2-2-2h-2V3h-2v2h-2V3H9v2H7c-1.1 0-2 .9-2 2v2H3v2h2v2H3v2h2v2c0 1.1.9 2 2 2h2v2h2v-2h2v2h2v-2h2c1.1 0 2-.9 2-2v-2h2v-2h-2v-2h2zm-4 6H7V7h10v10z',
  palette: 'M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-.99 0-.83.67-1.5 1.5-1.5H16c2.76 0 5-2.24 5-5 0-4.42-4.03-8-9-8zm-5.5 9c-.83 0-1.5-.67-1.5-1.5S5.67 9 6.5 9 8 9.67 8 10.5 7.33 12 6.5 12zm3-4C8.67 8 8 7.33 8 6.5S8.67 5 9.5 5s1.5.67 1.5 1.5S10.33 8 9.5 8zm5 0c-.83 0-1.5-.67-1.5-1.5S13.67 5 14.5 5s1.5.67 1.5 1.5S15.33 8 14.5 8zm3 4c-.83 0-1.5-.67-1.5-1.5S16.67 9 17.5 9s1.5.67 1.5 1.5-.67 1.5-1.5 1.5z',
  play_arrow: 'M8 5v14l11-7z',
  priority_high: 'M10 3h4v12h-4zM12 17a2 2 0 1 1 0 4 2 2 0 1 1 0-4z',
  refresh: 'M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z',
  stop: 'M6 6h12v12H6z',
  storage: 'M2 20h20v-4H2v4zm2-3h2v2H4v-2zM2 4v4h20V4H2zm4 3H4V5h2v2zm-4 7h20v-4H2v4zm2-3h2v2H4v-2z',
  sync: 'M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46C19.54 15.03 20 13.57 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74C4.46 8.97 4 10.43 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z',
};
const icon = (name, size = 20) => `<svg class="i" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="${PATHS[name]}"/></svg>`;
const ARROW = icon('arrow_forward', 18);
const INFO = icon('info');
const BACK_LABEL = `${icon('arrow_back', 18)}Back`;
const CHECK_BIG = icon('check', 28);

async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || ('Request failed (' + res.status + ')'));
  return data;
}

function duration(seconds) {
  if (seconds == null) return '—';
  const s = Math.round(seconds);
  return Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's';
}

// ---------------------------------------------------------------- catalog helpers
function presetProblem(preset, version) {
  for (const name of preset.apps) {
    const app = S.catalog.apps[name];
    if (!app.branches[version]) return app.title + ' has no ' + version + ' branch';
  }
  return '';
}

function applyPreset(id) {
  S.preset = id;
  if (!S.folderEdited) S.folder = '~/git/' + id;
  if (!S.siteEdited) S.site = id + '.localhost';
}

function pickVersion(id) {
  S.version = id;
  if (presetProblem(S.catalog.presets[S.preset], id)) applyPreset(S.catalog.default_preset);
}

function syncApps() {
  const backupApps = restoring() && S.backup ? Object.keys(S.backup.apps) : [];
  const onBench = isExisting() ? S.bench.apps : [];
  const onSite = isExisting() && !S.benchNew ? S.bench.sites[S.benchSite].apps : [];
  const key = [S.mode, S.version, S.preset, backupApps.join(','), isExisting() ? S.bench.folder + '|' + (S.benchNew ? '+new' : S.benchSite) : ''].join('|');
  if (key === S.appsKey) return;
  S.appsKey = key;
  S.depWarn = null;
  const next = {};
  const presetApps = isExisting() ? [] : S.catalog.presets[S.preset].apps;
  for (const [name, app] of Object.entries(S.catalog.apps)) {
    const branch = app.branches[S.version] || '';
    const inBackup = backupApps.includes(name) && name !== 'frappe';
    const installed = onSite.includes(name);
    next[name] = {
      title: app.title, description: app.description, repo: app.repo, requires: app.requires,
      private: app.private, local: !!app.local, custom: false, branch, available: !!branch || onBench.includes(name),
      locked: name === 'frappe' || installed, inBackup, fromPreset: presetApps.includes(name),
      onBench: onBench.includes(name), installed,
      selected: (!!branch || onBench.includes(name)) && (name === 'frappe' || installed || presetApps.includes(name) || inBackup),
    };
  }
  // Apps the bench has that the catalog does not know: installable on the site, not fetched again.
  for (const name of onBench) {
    if (next[name]) continue;
    const installed = onSite.includes(name);
    const inBackup = backupApps.includes(name);
    next[name] = { title: name, description: '', repo: '', requires: [], custom: false, branch: '', available: true,
      locked: installed, selected: installed || inBackup, onBench: true, installed, inBackup };
  }
  for (const [name, app] of Object.entries(S.apps)) if (app.custom && !next[name]) next[name] = app;
  S.apps = next;
  for (const name of Object.keys(next)) if (next[name].selected) selectRequired(name);
}

function selectRequired(name) {
  for (const req of S.apps[name].requires || []) {
    const dep = S.apps[req];
    if (dep && dep.available && !dep.selected) { dep.selected = true; selectRequired(req); }
  }
}

function dependants(name) {
  const out = [];
  const visit = (target) => {
    for (const [other, app] of Object.entries(S.apps)) {
      if (app.selected && !out.includes(other) && (app.requires || []).includes(target)) { out.push(other); visit(other); }
    }
  };
  visit(name);
  return out;
}

const isExisting = () => S.mode === 'existing' && !!S.bench;
const isMigrate = () => isExisting() && S.benchMigrate;
const migrateList = () => Object.keys(S.migrateSites).filter((n) => S.migrateSites[n]);
// A site restored from a backup: the new bench's "Restore" choice, or a new site in a loaded bench.
const restoring = () => (isExisting() ? S.benchNew : S.siteData === 'restore');

const selectedApps = () => Object.entries(S.apps).filter(([, a]) => a.selected);

// ---------------------------------------------------------------- rendering
function render() {
  const index = STEPS.findIndex(([id]) => id === S.step);
  S.reached = Math.max(S.reached, index);
  const locked = !!S.run;
  $('progress').style.width = index < 0 ? '0' : Math.round(((index + (S.step === 'done' ? 1 : 0.5)) / STEPS.length) * 100) + '%';
  $('steps').innerHTML = STEPS.map(([id, label], i) => {
    const failed = id === 'run' && S.run && ['failed', 'cancelled'].includes(S.run.state);
    const cls = id === S.step ? 'current' : (i < index ? 'done' : '');
    const mark = cls === 'done' ? icon('check', 16) : i + 1;
    const canGo = !locked && i < index && i < 4;  // forward moves go through Continue and its checks
    const inner = `<span class="n">${mark}</span>${esc(label)}`;
    return canGo ? `<button class="vstep link ${cls}" data-act="goto" data-arg="${id}">${inner}</button>`
      : `<span class="vstep ${cls} ${failed ? 'failed' : ''}">${inner}</span>`;
  }).join('');
  $('rail-foot').textContent = S.runs.some((r) => r.state === 'running') ? 'Closing this window does not stop runs. Reopen the URL shown in the terminal to come back.'
    : 'Nothing is written to disk until you press Start on the Review screen.';
  // Up to Review only: once Start is pressed, the run has the answers and the wizard is fresh again anyway.
  $('rail-reset').innerHTML = !locked && index >= 0 && index < 4
    ? `<button class="btn sm" data-act="start-over" title="Clear every choice and go back to the Project step">${icon('restart_alt', 18)}Start over</button>` : '';
  paintRuns();
  const wide = S.step === 'run' || S.step === 'projects' || S.step === 'bench';
  $('view').className = 'col' + (wide ? ' wide' : '');
  $('footer').className = 'in2' + (wide ? ' wide' : '');
  const [view, footer] = VIEWS[S.step]();
  $('view').innerHTML = view;
  $('footer').innerHTML = footer;
  if (S.step === 'run') paintLog();
  if (S.step === 'bench') paintBenchLog();
}

const field = (label, key, opts = {}) => `
  <label class="f"><span>${label}</span>
    <input class="in ${opts.mono ? 'mono' : ''} ${S.errors[key] ? 'bad' : ''}" data-bind="${key}"
      type="${opts.type || 'text'}" value="${esc(opts.value)}" placeholder="${esc(opts.placeholder || '')}"
      autocomplete="off" spellcheck="false">
    ${S.errors[key] ? `<span class="err">${esc(S.errors[key])}</span>` : (opts.hint ? `<span class="hint">${opts.hint}</span>` : '')}
  </label>`;

const radio = (act, arg, on, title, desc, extra = '', disabled = false) => `
  <button type="button" class="opt ${on ? 'sel' : ''}" role="radio" aria-checked="${on}" data-act="${act}"
    data-arg="${esc(arg)}" ${disabled ? 'disabled' : ''}>
    <span class="dot"><i></i></span><span><span class="t">${title}</span><span class="d">${esc(desc)}</span>${extra}</span>
  </button>`;

const modeCard = () => `<section class="card"><h2>Bench</h2><div class="grid2" role="radiogroup" aria-label="Bench">
  ${radio('mode', 'new', S.mode === 'new', 'Create a new bench', 'Set up Frappe, the apps and a site from scratch.')}
  ${radio('mode', 'existing', S.mode === 'existing', 'Add apps to an existing bench', 'Load a bench on this machine, then install more apps on one of its sites, add a new site from a backup, or migrate its sites.')}
</div></section>`;

function viewExisting() {
  const b = S.bench;
  const sites = !b ? '' : Object.entries(b.sites).map(([name, site]) => radio('bench-site', name, name === S.benchSite,
    `<span class="mono">${esc(name)}</span>`, site.apps ? 'Installed: ' + site.apps.join(', ') : 'Cannot read its apps: ' + site.error,
    '', !site.apps)).join('') + radio('bench-new', '', S.benchNew, 'New site from a backup',
    'Restore a backup, for example from production, as a new site of this bench to look into an issue.')
    + radio('bench-migrate', '', S.benchMigrate, 'Migrate sites', 'Run bench migrate on several sites of this bench at the same time.');
  const migrate = !b || !S.benchMigrate ? '' : `<section class="card"><h2>Sites to migrate</h2>
    ${Object.keys(b.sites).map((name) => `<label class="tog"><input type="checkbox" data-msite="${esc(name)}" ${S.migrateSites[name] ? 'checked' : ''}><span class="mono">${esc(name)}</span></label>`).join('')}
    <label class="tog"><input type="checkbox" data-check="migrateBackup" ${S.migrateBackup ? 'checked' : ''}>Back up each site first, with its files</label>
    <p class="lead small">Each site migrates in its own process, side by side, with its own log.</p></section>`;
  const ready = b && (S.benchSite || S.benchNew || (S.benchMigrate && migrateList().length));
  const newSite = !b || !S.benchNew ? '' : `<section class="card"><h2>New site</h2><div class="grid2">
      ${field('Site name', 'site', { mono: true, value: S.site, hint: 'Chrome and Firefox open a name ending in .localhost without a hosts entry; Safari needs one in /etc/hosts' })}
      ${field('Database name <span class="hint">(optional)</span>', 'db', { mono: true, value: S.db, placeholder: 'Leave empty to let bench choose' })}
      ${field('Administrator password', 'pw', { type: 'password', value: S.pw, hint: 'Set on the restored site, replacing the one from the backup' })}
      ${field('MariaDB root password', 'rootPw', { type: 'password', value: S.rootPw, hint: 'Needed to create the new database' })}
    </div>${restoreFields()}</section>`;
  const view = `<div class="head"><h1>Project</h1><p class="lead">Load a bench that already exists, then pick a site to add apps to, add a new site from a backup, or migrate several sites at once.</p></div>
    ${modeCard()}
    <section class="card"><h2>Existing bench</h2><div class="grid2 end">
      ${field('Bench folder', 'benchFolder', { mono: true, value: S.benchFolder, placeholder: '~/frappe-bench', hint: 'The folder that holds apps/ and sites/' })}
      <div><button class="btn primary" data-act="load-bench" ${S.benchBusy ? 'disabled' : ''}>${S.benchBusy ? '<span class="spin"></span> Reading bench…' : 'Load bench'}</button></div>
    </div>
    ${S.benchMsg ? `<div class="note bad">${INFO}<span>${esc(S.benchMsg)}</span></div>` : ''}
    ${b ? `<p class="lead small">Frappe <strong>${esc(S.catalog.frappe_versions[b.version].title)}</strong> · Node ${esc(b.node || profile().node)} · apps in the bench: <span class="mono">${esc(b.apps.join(', '))}</span></p>` : ''}</section>
    ${b ? `<section class="card"><h2>Site</h2><div class="grid2" role="radiogroup" aria-label="Site">${sites}</div>
      ${S.errors.benchSite ? `<span class="err">${esc(S.errors.benchSite)}</span>` : ''}</section>` : ''}
    ${newSite}${migrate}`;
  const footer = `<div class="live"></div>
    <div class="right"><button class="btn primary" data-act="next" ${ready ? '' : 'disabled'}>Continue to ${S.benchMigrate ? 'review' : 'apps'} ${ARROW}</button></div>`;
  return [view, footer];
}

function restoreFields() {
  const r = S.restore;
  const backupLine = S.backupMsg ? `<span class="err">${esc(S.backupMsg)}</span>`
    : S.backup && S.backupPath === r.sql ? `<span class="okmsg">${Object.keys(S.backup.apps).length
      ? 'Found in backup: ' + esc(Object.entries(S.backup.apps).map(([n, v]) => n + ' ' + v).join(', '))
      : 'Could not read the app list from this backup; app checks will be skipped'}</span>` : '';
  return `
    <div class="grid2">
      <div>${field('Database backup', 'restore.sql', { mono: true, value: r.sql, placeholder: '~/backups/site-database.sql.gz', hint: 'Path to a .sql or .sql.gz file on this machine' })}${backupLine}</div>
      ${field('Public files <span class="hint">(optional)</span>', 'restore.public', { mono: true, value: r.public, placeholder: '~/backups/site-files.tar' })}
      ${field('Private files <span class="hint">(optional)</span>', 'restore.private', { mono: true, value: r.private, placeholder: '~/backups/site-private-files.tar' })}
      ${field('Backup encryption key <span class="hint">(optional)</span>', 'restore.backup_key', { type: 'password', value: r.backup_key, hint: 'Only for backups that were encrypted when taken' })}
      ${field('Source site encryption key <span class="hint">(optional)</span>', 'restore.site_key', { type: 'password', value: r.site_key, hint: 'encryption_key from the source site_config.json. Without it, passwords stored inside the site cannot be decrypted.' })}
    </div>
    <label class="tog"><input type="checkbox" data-check="restore.mute_emails" ${r.mute_emails ? 'checked' : ''}><span><strong>Mute outgoing email</strong><br><span class="hint">So the restored copy cannot email real customers</span></span></label>
    <label class="tog"><input type="checkbox" data-check="restore.pause_scheduler" ${r.pause_scheduler ? 'checked' : ''}><span><strong>Pause scheduler</strong><br><span class="hint">So background jobs and integrations from the source do not run</span></span></label>
    <label class="tog"><input type="checkbox" data-check="restore.sanitize" ${r.sanitize ? 'checked' : ''}><span><strong>Hold back production side effects</strong><br><span class="hint">Marks queued email as not to be sent, and switches off incoming mail, webhooks and SMS or Slack alerts</span></span></label>
    <p class="lead small">Paths are typed or pasted: a browser cannot tell this tool where a chosen file lives.</p>`;
}

function viewProject() {
  if (S.mode === 'existing') return viewExisting();
  const c = S.catalog;
  const versions = Object.entries(c.frappe_versions).filter(([, v]) => !v.existing_only).map(([id, v]) => radio('version', id, id === S.version,
    esc(v.title) + (v.default ? ' <span class="badge blue">Default</span>' : ''),
    `Python ${v.python} · Node ${v.node} · branch ${id}`)).join('');
  const presets = Object.entries(c.presets).map(([id, p]) => {
    const why = presetProblem(p, S.version);
    return radio('preset', id, id === S.preset, esc(p.title) + (p.local ? ' <span class="badge">Local</span>' : ''),
      p.description, why ? `<span class="why">${esc(why)}</span>` : '', !!why);
  }).join('');
  const restore = S.siteData !== 'restore' ? '' : restoreFields();
  const view = `
    <div class="head"><h1>Project</h1><p class="lead">Pick the Frappe version and a preset. Folder and site name follow the preset until you edit them.</p></div>
    ${modeCard()}
    <section class="card"><h2>Frappe version</h2><div class="grid2" role="radiogroup" aria-label="Frappe version">${versions}</div>
      <p class="lead small">Changing the version re-runs the system check and re-checks which presets and apps have a branch for it.</p></section>
    <section class="card"><h2>Preset</h2><div class="grid3" role="radiogroup" aria-label="Preset">${presets}</div></section>
    <section class="card"><h2>Bench and site</h2><div class="grid2">
      ${field('Bench folder', 'folder', { mono: true, value: S.folder, hint: 'Must not exist yet' })}
      ${field('Site name', 'site', { mono: true, value: S.site, hint: 'Opens at http://localhost:8000 under bench start' })}
      ${field('Database name <span class="hint">(optional)</span>', 'db', { mono: true, value: S.db, placeholder: 'Leave empty to let bench choose' })}
      ${field('Administrator password', 'pw', { type: 'password', value: S.pw, hint: 'Login for the Administrator user on the new site' })}
    </div></section>
    <section class="card"><h2>Site data</h2><div class="grid2" role="radiogroup" aria-label="Site data">
      ${radio('sitedata', 'new', S.siteData === 'new', 'New empty site', 'Install the selected apps on a fresh database.')}
      ${radio('sitedata', 'restore', S.siteData === 'restore', 'Restore from backup', 'Load a database backup, with its files, then migrate.')}
    </div>${restore}</section>`;
  const p = profile();
  const footer = `<div class="live" id="live">${liveSummary()}</div>
    <div class="right"><button class="btn primary" data-act="next">Continue to system check ${ARROW}</button></div>`;
  return [view, footer, p];
}

const liveSummary = () => `<span>Site <b class="mono">${esc(S.site)}</b></span><span>Folder <b class="mono">${esc(S.folder)}</b></span>
  <span>Python <b>${esc(profile().python)}</b></span><span>Node <b>${esc(profile().node)}</b></span>`;

const STATUS = { ok: 'OK', missing: 'Missing', needs_fix: 'Needs fix', installing: 'Installing' };

function viewCheck() {
  const rows = S.checks || [];
  const bad = rows.filter((r) => r.status !== 'ok');
  const table = S.checking && !rows.length ? '<p class="lead" style="padding:24px"><span class="spin"></span> Checking this machine…</p>' : `
    <div style="overflow-x:auto"><table class="tbl"><thead><tr><th style="width:36%">Prerequisite</th><th>Required</th><th>Found</th><th>Status</th></tr></thead><tbody>
    ${rows.map((r) => {
    const status = S.installing === r.id ? 'installing' : r.status;
    const log = S.installLogs[r.id];
    return `<tr><td><strong>${esc(r.title)}</strong>${r.note ? `<div class="sub">${esc(r.note)}</div>` : ''}
      ${log ? `<details ${r.status !== 'ok' ? 'open' : ''}><summary class="sub">Install log</summary><pre>${esc(log)}</pre></details>` : ''}</td>
      <td class="mono">${esc(r.required)}</td><td class="mono">${esc(r.found) || '<span style="color:var(--muted)">—</span>'}</td>
      <td><span class="st ${status}">${status === 'installing' ? '<span class="spin"></span>' : '<i class="pip"></i>'} ${STATUS[status]}</span></td></tr>`;
  }).join('')}</tbody></table></div>`;
  const missing = bad.filter((r) => r.status === 'missing').length;
  const fix = bad.length - missing;
  const summary = [missing ? missing + ' missing' : '', fix ? fix + ' needs fix' : ''].filter(Boolean).join(', ');
  const install = !bad.length ? '' : `<section class="card">
    <div class="row"><h2>${esc(summary)}</h2><span class="lead small">${S.mac
      ? 'Homebrew, nvm and bench install as you; only wkhtmltopdf (and Rosetta on Apple silicon) needs <span class="mono">sudo</span>.'
      : 'Root commands run through <span class="mono">sudo</span>; nvm, Node, yarn and bench install as you.'}</span></div>
    <div class="grid2 end">
      ${field('sudo password', 'sudo', { type: 'password', value: S.sudo, placeholder: 'Your account password', hint: 'Kept in memory for this session only, never written to disk' })}
      <div><button class="btn primary" data-act="install" ${S.installing ? 'disabled' : ''}>${S.installing ? '<span class="spin"></span> Installing…' : 'Install missing'}</button></div>
    </div>
    ${S.installMsg ? `<div class="note bad">${INFO}<span>${esc(S.installMsg)}</span></div>` : ''}</section>`;
  const mariadbReady = rows.some((r) => r.id === 'mariadb' && r.status === 'ok');
  const root = !mariadbReady ? '' : `<section class="card"><h2>MariaDB root password</h2>
    <p class="lead small">The wizard needs MariaDB's root password to create the site database.</p>
    <div class="grid2 end">
      ${field('Root password', 'rootPw', { type: 'password', value: S.rootPw, placeholder: 'MariaDB root password' })}
      <div><button class="btn" data-act="verify-root" ${S.rootBusy ? 'disabled' : ''}>Verify</button></div>
    </div>
    <div id="root-note">${S.rootOk === true ? '<div class="note info">' + INFO + '<span><strong>Verified.</strong> This password works for MariaDB root.</span></div>' : ''}
    ${S.rootOk === false ? `<div class="note">${INFO}<span><strong>MariaDB did not accept this password.</strong> If it is a typo, correct it and verify again.
      If root has no password yet (a fresh install, or root reachable only through <span class="mono">sudo</span>), the wizard can set the password above as the root password. This changes how root logs in to MariaDB on this machine.
      <span class="acts"><button class="btn sm" data-act="set-root" ${S.rootBusy ? 'disabled' : ''}>Set this as the root password</button></span>
      ${S.rootMsg ? `<br><span class="err">${esc(S.rootMsg)}</span>` : ''}</span></div>` : ''}</div></section>`;
  const ready = rows.length && !bad.length && S.rootOk === true;
  const view = `<div class="head"><h1>System check</h1><p class="lead">Prerequisites for Frappe <strong>${esc(profile().title)}</strong> on this machine. Only failing items are installed.</p></div>
    <section class="card flush">${table}</section>${install}${root}`;
  const footer = `<button class="btn" data-act="back">${BACK_LABEL}</button>
    <div class="right"><button class="btn" data-act="recheck" ${S.checking || S.installing ? 'disabled' : ''}>Re-check</button>
    <span class="lead small">${ready ? '' : 'Continue unlocks once every row is OK and the root password is verified'}</span>
    <button class="btn primary" data-act="next" ${ready ? '' : 'disabled'}>Continue to apps ${ARROW}</button></div>`;
  return [view, footer];
}

function viewApps() {
  syncApps();
  const cards = Object.entries(S.apps).map(([name, a]) => {
    const badges = [
      a.locked ? '<span class="badge blue">Locked</span>' : '',
      a.private ? '<span class="badge amber">Private repo</span>' : '',
      a.custom ? '<span class="badge">Custom</span>' : '',
      a.credentials ? '<span class="badge amber" title="Taken from the URL you gave. Kept in memory until the wizard closes; not written to disk.">Token held for this session</span>' : '',
      a.local ? '<span class="badge">Local</span>' : '',
      a.inBackup ? '<span class="badge green">in backup</span>' : '',
      a.installed ? '<span class="badge green">installed on site</span>' : a.onBench ? '<span class="badge">in the bench</span>' : '',
      a.fromPreset && a.selected && !a.locked ? '<span class="badge">from preset</span>' : '',
      !a.available ? `<span class="badge amber">Not available for ${esc(profile().title)}</span>` : '',
    ].join(' ');
    const needs = (a.requires || []).filter((r) => r !== 'frappe');
    return `<div class="app ${a.selected ? 'sel' : ''} ${a.available ? '' : 'dis'}">
      <input type="checkbox" data-app="${esc(name)}" ${a.selected ? 'checked' : ''} ${a.locked || !a.available ? 'disabled' : ''} aria-label="${esc(a.title)}">
      <div class="body"><div class="t">${esc(a.title)} ${badges}</div><div class="d">${esc(a.description)}</div>
        <div class="repo mono">${esc(a.repo)}</div>
        <div class="br"><span style="color:var(--muted)">Branch</span>
          <input class="sm" data-branch="${esc(name)}" value="${esc(a.onBench ? 'already in the bench' : a.branch)}" ${a.locked || !a.available || a.onBench ? 'disabled' : ''} aria-label="Branch for ${esc(a.title)}">
          ${needs.length ? `<span class="badge">requires ${esc(needs.join(', '))}</span>` : ''}
          ${a.custom ? `<button class="btn sm" data-act="remove-custom" data-arg="${esc(name)}">Remove</button>` : ''}</div>
      </div></div>`;
  }).join('');
  const add = `<div class="app add"><div class="t">${icon('add', 20)}Add a custom app</div>
    ${field('Git URL', 'custom.repo', { mono: true, value: S.custom.repo, placeholder: 'https://github.com/org/app.git or git@github.com:org/app.git' })}
    ${field('Branch', 'custom.branch', { mono: true, value: S.custom.branch, placeholder: S.version })}
    ${S.customMsg ? `<span class="err">${esc(S.customMsg)}</span>` : '<span class="hint">The app name and what it requires are read from the repository.</span>'}
    <div><button class="btn sm" data-act="add-custom" ${S.customBusy ? 'disabled' : ''}>${S.customBusy ? '<span class="spin"></span> Reading repository…' : 'Add app'}</button></div></div>`;
  const importCard = `<section class="card"><div class="row"><h2>Paste an app list</h2><span class="lead small">JSON: <span class="mono">[{"url": "…", "branch": "…"}]</span></span></div>
    <textarea class="in mono area" data-bind="importText" rows="5" spellcheck="false" placeholder='[{"url": "https://github.com/frappe/hrms.git", "branch": "v15.62.0"}]'>${esc(S.importText)}</textarea>
    <span class="hint">A branch can be a branch or a tag. Apps with no declared requirements are installed in the order of the list. A token inside a URL is removed from it and kept in memory for this session only.</span>
    ${S.importMsg ? `<span class="${S.importErrors.length ? 'err' : 'okmsg'}">${esc(S.importMsg)}</span>` : ''}
    ${S.importErrors.length ? `<div class="note bad">${INFO}<span>${S.importErrors.map((e) => `<span class="mono">${esc(e.repo)}</span> (${esc(e.branch)}): ${esc(e.error)}`).join('<br>')}</span></div>` : ''}
    <div><button class="btn sm" data-act="import" ${S.importBusy ? 'disabled' : ''}>${S.importBusy ? '<span class="spin"></span> Reading repositories…' : 'Add apps from list'}</button></div></section>`;
  const warn = !S.depWarn ? '' : `<div class="note">${INFO}<span><strong>${esc(S.apps[S.depWarn.app].title)} is required by ${esc(S.depWarn.others.map((n) => S.apps[n].title).join(', '))}.</strong>
    Deselecting it deselects those too.<span class="acts"><button class="btn sm" data-act="dep-all">Deselect all</button><button class="btn sm" data-act="dep-keep">Keep</button></span></span></div>`;
  const names = selectedApps().map(([n]) => n);
  const intro = isExisting() && S.benchNew ? `New site <strong class="mono">${esc(S.site)}</strong> from a backup in <span class="mono">${esc(tilde(S.bench.folder))}</span> on Frappe <strong>${esc(profile().title)}</strong>.
    Apps in the backup are selected. Apps in the bench are installed without being fetched again; the rest are fetched first.`
    : isExisting() ? `Site <strong class="mono">${esc(S.benchSite)}</strong> in <span class="mono">${esc(tilde(S.bench.folder))}</span> on Frappe <strong>${esc(profile().title)}</strong>.
    Apps already installed on the site are locked. Apps in the bench are installed without being fetched again; the rest are fetched first.`
    : `Preset <strong>${esc(S.catalog.presets[S.preset].title)}</strong> on Frappe <strong>${esc(profile().title)}</strong>.`;
  const view = `<div class="head"><h1>Apps</h1><p class="lead">${intro}
    Selecting an app also selects what it requires. The install order is worked out from each app's own requirements on the next screen.</p>
    ${S.catalog.github_ssh_host ? `<p class="lead small">GitHub repositories are fetched with the SSH key of <span class="mono">${esc(S.catalog.github_ssh_host)}</span> from <span class="mono">~/.ssh/config</span>, set in <span class="mono">~/.config/bench-wizard/settings.json</span>.</p>` : ''}</div>
    ${warn}<div class="grid2">${cards}${add}</div>${marketCard()}${extensionCard()}${importCard}`;
  const footer = `<button class="btn" data-act="back">${BACK_LABEL}</button><div class="right">
    <span class="lead small">${names.length} app${names.length === 1 ? '' : 's'} selected: ${esc(names.join(', '))}</span>
    <button class="btn primary" data-act="next">Continue to review ${ARROW}</button></div>`;
  return [view, footer];
}

function marketCard() {
  const m = S.mp;
  const listed = m && m.versions.includes(S.version);
  const found = !m ? '' : `<div class="app sel"><div class="body">
      <div class="t">${esc(m.title || m.name)} <span class="badge ${m.free ? 'green' : 'amber'}">${m.free ? 'Free' : 'Paid'}</span>
        ${m.versions.map((v) => `<span class="badge ${v === S.version ? 'blue' : ''}">${esc(S.catalog.frappe_versions[v] ? S.catalog.frappe_versions[v].title : v.replace('version-', 'Version '))}</span>`).join(' ')}
        ${m.nightly ? '<span class="badge">Nightly</span>' : ''}</div>
      ${listed ? '' : `<div class="note">${INFO}<span>The marketplace does not list this app for Frappe <strong>${esc(profile().title)}</strong>. It may still install from a branch that supports it.</span></div>`}
      ${m.free ? '' : `<div class="note">${INFO}<span>A paid app on Frappe Cloud. Its repository may be private; adding it works only if your Git account can read it.</span></div>`}
      ${m.repo ? `<div class="repo mono">${esc(m.repo)}</div>` : '<span class="err">Its marketplace page links no GitHub repository, so it cannot be fetched from here.</span>'}
      ${m.branches.length ? `<div class="br"><span style="color:var(--muted)">Branch</span>
          <select class="in sm" data-bind="mpBranch" aria-label="Branch of ${esc(m.name)}">${m.branches.map((b) => `<option ${b === S.mpBranch ? 'selected' : ''}>${esc(b)}</option>`).join('')}</select>
          <button class="btn sm primary" data-act="mp-add" ${S.customBusy ? 'disabled' : ''}>${S.customBusy ? '<span class="spin"></span> Reading repository…' : 'Add app'}</button></div>`
        : m.repo ? `<span class="err">Cannot list its branches: ${esc(m.branch_error || 'the repository has none')}</span>` : ''}
      ${S.customMsg ? `<span class="err">${esc(S.customMsg)}</span>` : ''}</div></div>`;
  return `<section class="card"><div class="row"><h2>Frappe Cloud Marketplace</h2><span class="lead small">${S.mpApps ? S.mpApps.length + ' published apps' : ''}</span></div>
    <div class="grid2 end">
      <label class="f"><span>App</span><input class="in" data-bind="mpQuery" list="mp-list" value="${esc(S.mpQuery)}" placeholder="${S.mpApps ? 'Type to search, e.g. HRMS' : 'Loading the marketplace…'}" autocomplete="off" spellcheck="false"></label>
      <div><button class="btn" data-act="mp-lookup" ${S.mpBusy || !S.mpApps ? 'disabled' : ''}>${S.mpBusy ? '<span class="spin"></span> Looking up…' : 'Look up'}</button></div>
    </div>
    <datalist id="mp-list">${(S.mpApps || []).map((a) => `<option value="${esc(a.title)}"></option>`).join('')}</datalist>
    ${S.mpMsg ? `<span class="err">${esc(S.mpMsg)}</span>` : '<span class="hint">Repository, supported versions and price come from the app\'s public marketplace page; its branches from the repository.</span>'}
    ${found}</section>`;
}

async function loadMarket() {
  if (S.mpApps) return;
  try { S.mpApps = (await api('GET', '/api/marketplace')).apps; S.mpMsg = ''; } catch (err) { S.mpMsg = err.message; }
  if (S.step === 'apps') render();
}

async function lookupMarket() {
  const q = S.mpQuery.trim().toLowerCase();
  const app = (S.mpApps || []).find((a) => a.title.toLowerCase() === q || a.name.toLowerCase() === q);
  if (!app) { S.mpMsg = 'Pick an app from the list'; return render(); }
  S.mpBusy = true; S.mpMsg = ''; S.mp = null; S.customMsg = ''; render();
  try {
    const res = await api('POST', '/api/marketplace/app', { name: app.name, version: S.version });
    if (res.ok) { S.mp = Object.assign(res, { title: app.title }); S.mpBranch = res.branches[0] || ''; } else S.mpMsg = res.error;
  } catch (err) { S.mpMsg = err.message; }
  S.mpBusy = false; render();
}

// ---------------------------------------------------------------- projects (every bench on this machine)
const gb = (n) => (n / 1024 ** 3).toFixed(1) + ' GB';
const PORT_COLS = [['webserver_port', 'Web'], ['socketio_port', 'Socket.IO'], ['file_watcher_port', 'Watcher'], ['redis_cache', 'Redis cache'], ['redis_queue', 'Redis queue']];

function viewProjects() {
  const p = S.proj;
  const head = `<div class="head"><h1>Projects</h1><p class="lead">Every bench under your home folder, and any you added. Migrate or build a site, and give each bench ports of its own so several can run at once.</p></div>`;
  const footer = `<span class="lead small">${p ? p.benches.length + (p.benches.length === 1 ? ' bench' : ' benches') : ''}</span><div class="right"><button class="btn" data-act="projects" ${S.projBusy ? 'disabled' : ''}>${icon('refresh', 18)}Refresh</button></div>`;
  if (!p) return [head + `<p class="lead">${S.projMsg ? esc(S.projMsg) : '<span class="spin"></span> Looking for benches…'}</p>`, footer];
  const r = p.resources;
  const running = p.benches.filter((b) => b.running);
  const held = running.reduce((sum, b) => sum + (b.rss || 0), 0);
  const LOW = { memory: 2 * 1024 ** 3, disk: 10 * 1024 ** 3 };  // projects.LOW_MEMORY and LOW_DISK
  const stat = (ic, label, value, used, low) => `<div class="stat"><span class="si">${icon(ic, 22)}</span><div>
      <div class="k">${label}</div><div class="v">${value}</div>
      ${used == null ? '' : `<div class="meter ${low ? 'low' : ''}" role="img" aria-label="${Math.round(used * 100)}% in use"><i style="width:${Math.round(used * 100)}%"></i></div>`}</div></div>`;
  const res = `<section class="card"><h2>This machine</h2><div class="stats">
      ${stat('memory', 'Memory free', r.memory ? `${gb(r.memory.available)} <small>of ${gb(r.memory.total)}</small>` : 'unknown',
    r.memory && 1 - r.memory.available / r.memory.total, r.memory && r.memory.available < LOW.memory)}
      ${stat('storage', 'Disk free', `${gb(r.disk.free)} <small>of ${gb(r.disk.total)}</small>`, 1 - r.disk.free / r.disk.total, r.disk.free < LOW.disk)}
      ${stat('dns', 'Benches running', p.running == null ? 'cannot tell here' : `${p.running}${held ? ` <small>using ${gb(held)}</small>` : ''}`)}
    </div>${r.warnings.map((w) => `<div class="note bad">${INFO}<span>${esc(w)}</span></div>`).join('')}</section>`;
  const clashing = p.benches.filter((b) => b.clashes.length).length;
  const edits = Object.keys(S.portEdits).length;
  const shown = S.projFilter === 'running' ? running : p.benches;
  const rows = shown.map((b) => {
    const e = S.portEdits[b.folder] || {};
    const site = S.projSite[b.folder] || '';
    const busy = S.projBusy === b.folder;
    return `<tr><td><div class="bench"><span class="av ${b.running ? 'on' : ''}">${icon('dns')}</span><div class="nm"><button class="linkbtn mono" data-act="bench-open" data-arg="${esc(b.folder)}" title="Sites, apps, backups and logs of this bench">${esc(tilde(b.folder)).replace(/\//g, '/<wbr>')}</button>
        <div class="sub">${esc(b.version_title)}${b.running ? `<span class="state-on"><i class="pip"></i>Running${b.rss ? ', ' + gb(b.rss) : ''}</span>` : ''}</div></div></div></td>
      <td><select class="in sm" data-projsite="${esc(b.folder)}" aria-label="Site of ${esc(tilde(b.folder))}" ${b.sites.length ? '' : 'disabled'}>
        ${b.sites.length ? `<option value="">All sites (${b.sites.length})</option>` + b.sites.map((n) => `<option ${n === site ? 'selected' : ''}>${esc(n)}</option>`).join('') : '<option>no sites</option>'}</select></td>
      <td class="acts">${b.running ? `<button class="btn sm stop" data-act="proj-stop" data-arg="${esc(b.folder)}" ${busy ? 'disabled' : ''} title="Stop" aria-label="Stop">${icon('stop', 18)}<span class="lbl">Stop</span></button>`
    : `<button class="btn sm tonal" data-act="proj-start" data-arg="${esc(b.folder)}" ${busy ? 'disabled' : ''} title="Start" aria-label="Start">${icon('play_arrow', 18)}<span class="lbl">Start</span></button>`}
        <button class="btn sm" data-act="proj-migrate" data-arg="${esc(b.folder)}" ${busy || !b.sites.length ? 'disabled' : ''} title="Migrate" aria-label="Migrate">${icon('sync', 18)}<span class="lbl">Migrate</span></button>
        <button class="btn sm" data-act="proj-build" data-arg="${esc(b.folder)}" ${busy ? 'disabled' : ''} title="Build" aria-label="Build">${icon('build', 18)}<span class="lbl">Build</span></button>
        <button class="btn sm primary" data-act="bench-open" data-arg="${esc(b.folder)}" title="Sites, apps, backups and logs: open as Administrator, back up, update apps">Manage${ARROW}</button></td>
      ${PORT_COLS.map(([key]) => {
    const value = e[key] != null ? e[key] : b.ports[key];
    const cls = e[key] != null && e[key] !== b.ports[key] ? 'changed' : b.clashes.includes(b.ports[key]) ? 'bad' : '';
    return `<td><input class="in sm port mono ${cls}" data-port="${esc(b.folder)}" data-key="${key}" value="${esc(value)}" inputmode="numeric" aria-label="${key} of ${esc(tilde(b.folder))}"></td>`;
  }).join('')}</tr>`;
  }).join('');
  const table = `<section class="card flush"><div class="loghead"><h2>Benches and ports</h2>
      <span class="lead small">${clashing ? `<span class="err">${clashing} benches share ports with another, so only one of them can run at a time.</span>` : 'No two benches share a port.'}</span>
      <div class="right"><button class="btn sm" data-act="ports-auto" ${clashing ? '' : 'disabled'}>${icon('auto_awesome', 18)}Auto-allocate ports</button>
      <button class="btn sm primary" data-act="ports-apply" id="ports-apply" ${edits ? '' : 'disabled'}>${icon('check', 18)}Apply port changes${edits ? ` (${edits})` : ''}</button></div></div>
    ${S.projNote ? `<div class="note info" style="margin:0 16px 12px">${INFO}<span>${esc(S.projNote)}</span></div>` : ''}
    <div class="filters" role="group" aria-label="Show benches">
      <button class="fchip" aria-pressed="${S.projFilter === 'all'}" data-act="proj-filter" data-arg="all">${S.projFilter === 'all' ? icon('check', 18) : ''}All <span>${p.benches.length}</span></button>
      <button class="fchip" aria-pressed="${S.projFilter === 'running'}" data-act="proj-filter" data-arg="running" ${p.running == null ? 'disabled title="This system cannot tell which benches are running"' : ''}>${S.projFilter === 'running' ? icon('check', 18) : ''}<i class="pip on"></i>Running <span>${running.length}</span></button>
    </div>
    <div style="overflow-x:auto"><table class="tbl proj"><thead><tr><th>Bench</th><th>Site</th><th></th>${PORT_COLS.map(([, l]) => `<th>${l}</th>`).join('')}</tr></thead>
    <tbody>${rows || `<tr><td colspan="8" class="sub">${S.projFilter === 'running' ? 'No bench is running. Show all benches and press Start on one.' : 'No benches found in the folders below. Add the folder that holds yours.'}</td></tr>`}</tbody></table></div>
    <p class="lead small" style="padding:0 16px 16px">New ports take effect the next time the bench starts; a running bench keeps its old ports until then. Highlighted ports are shared with another bench.</p></section>`;
  const forget = (f, what) => `<button class="chip-x" data-act="proj-remove" data-arg="${esc(f)}" aria-label="Stop ${what} ${esc(tilde(f))}" title="Remove from the list; nothing on disk changes">${icon('close', 16)}</button>`;
  const added = p.benches.filter((b) => b.added);
  const add = `<section class="card"><h2>Where benches are found</h2>
      <p class="lead small">Each folder here is searched three folders deep. Removing one only takes it off this list.</p>
      <div class="chips"><span class="chip path mono">${icon('folder', 16)}${esc(tilde(p.home))} <span class="via">home folder</span></span>
        ${p.folders.map((f) => `<span class="chip path mono">${icon('folder', 16)}${esc(tilde(f))}${forget(f, 'searching')}</span>`).join('')}
        ${added.map((b) => `<span class="chip path mono">${icon('dns', 16)}${esc(tilde(b.folder))}${forget(b.folder, 'listing')}</span>`).join('')}</div>
      <div class="grid2 end" style="margin-top:16px">
      ${field('Folder', 'addFolder', { mono: true, value: S.addFolder, placeholder: 'Type a path, or use Browse…', hint: 'A folder that holds benches, such as /srv/benches, or one bench folder' })}
      <div class="acts"><button class="btn" data-act="proj-add">${icon('create_new_folder', 18)}Add folder</button>
        <button class="btn" data-act="fx-open" ${S.fx ? 'disabled' : ''}>${icon('folder', 18)}Browse…</button></div></div>${explorer()}</section>`;
  return [head + (S.projMsg ? `<div class="note bad">${INFO}<span style="white-space:pre-wrap">${esc(S.projMsg)}</span></div>` : '') + res + table + add, footer];
}

// A folder browser in the page: the server lists folder names only, so it works on every system and marks benches.
function explorer() {
  const fx = S.fx;
  if (!fx) return '';
  // Inside the home folder the path starts at ~, as everywhere else on the page; elsewhere at / (the top of the disk).
  const home = S.catalog.home;
  const inHome = fx.path === home || (fx.path || '').startsWith(home + '/');
  const base = inHome ? home : '';
  const parts = (fx.path || '').slice(base.length).split('/').filter(Boolean);
  const crumbs = `<button data-act="fx-go" data-arg="${esc(base || '/')}" aria-label="${inHome ? 'Your home folder' : 'The top of the disk'}">${inHome ? '~' : '/'}</button>`
    + parts.map((part, i) => `${i || inHome ? '<span class="sep" aria-hidden="true">/</span>' : ''}<button data-act="fx-go" data-arg="${esc(base + '/' + parts.slice(0, i + 1).join('/'))}">${esc(part)}</button>`).join('');
  const rows = (fx.folders || []).map((f) => `<div class="fx-row"><button class="fx-open" data-act="fx-go" data-arg="${esc(f.path)}">${icon(f.bench ? 'dns' : 'folder', 18)}<span>${esc(f.name)}</span>${f.bench ? '<span class="badge blue">Bench</span>' : ''}</button>
      ${f.bench ? `<button class="btn sm tonal" data-act="fx-add" data-arg="${esc(f.path)}">Add bench</button>` : ''}</div>`).join('');
  return `<div class="fx" role="group" aria-label="Choose a folder">
    <div class="fx-head"><button class="btn sm" data-act="fx-go" data-arg="${esc(fx.parent || '')}" ${fx.parent && !fx.busy ? '' : 'disabled'}>${icon('arrow_upward', 18)}Up</button>
      <nav class="crumbs mono" aria-label="Folder path">${crumbs}</nav></div>
    ${fx.msg ? `<div class="note bad" style="margin:12px">${INFO}<span>${esc(fx.msg)}</span></div>` : ''}
    <div class="fx-list">${fx.busy ? '<p class="fx-empty"><span class="spin"></span> Opening…</p>' : rows || '<p class="fx-empty">No folders in here.</p>'}
      ${fx.more ? `<p class="fx-empty">and ${fx.more} more folders not shown</p>` : ''}</div>
    <div class="acts"><button class="btn sm primary" data-act="fx-add" data-arg="${esc(fx.path || '')}" ${fx.busy || !fx.path ? 'disabled' : ''}>${icon('check', 18)}${fx.bench ? 'Add this bench' : 'Search this folder'}</button>
      <button class="btn sm" data-act="fx-close">Cancel</button>
      <span class="lead small">${fx.bench ? 'This folder is a bench.' : 'Benches up to three folders below it are listed.'}</span></div></div>`;
}

async function browseTo(path) {
  S.fx = Object.assign(S.fx || {}, { busy: true, msg: '' }); render();
  try { Object.assign(S.fx, await api('GET', '/api/folders' + (path ? '?path=' + encodeURIComponent(path) : '')), { busy: false }); }
  catch (err) { S.fx.busy = false; S.fx.msg = err.message; }
  if (S.step !== 'projects') return;
  render();
  const first = document.querySelector('.fx-list button');
  if (first) first.focus({ preventScroll: true });
}

async function loadProjects() {
  S.projBusy = 'all'; S.projMsg = ''; render();
  try { S.proj = await api('GET', '/api/projects'); } catch (err) { S.projMsg = err.message; }
  S.projBusy = ''; if (S.step === 'projects') render();
}

// Plan, then start at once: these steps change nothing a user would want to review first.
async function startQuick(body, folder) {
  S.projBusy = folder; S.projMsg = ''; render();
  try {
    const plan = await api('POST', '/api/plan', body);
    if (!plan.ok) throw new Error(plan.checks.filter((c) => !c.ok).map((c) => c.label).join('; '));
    const res = await api('POST', '/api/run', {});
    S.projBusy = '';
    S.wizStep = S.wizStep || 'project';
    attachRun(res.run_id); refreshRuns();
    return res;
  } catch (err) { S.projMsg = (folder ? tilde(folder) + ': ' : '') + err.message; S.projBusy = ''; render(); }
  return null;
}

// Start or stop a bench; `bench start` runs apart from the wizard, so it outlives it.
async function benchPower(folder, what) {
  S.projBusy = folder; S.projMsg = ''; S.projNote = `${what === 'start' ? 'Starting' : 'Stopping'} ${tilde(folder)}…`; render();
  try {
    const res = await api('POST', '/api/bench/' + what, { folder });
    S.projNote = what === 'stop' ? `${tilde(folder)} stopped.`
      : `${tilde(folder)} is running, web on port ${res.port}. It keeps running after you close the wizard; its output is in ${tilde(res.log)}.`;
    loadProjects();
  } catch (err) { S.projNote = ''; S.projMsg = tilde(folder) + ': ' + err.message; S.projBusy = ''; render(); }
}

async function applyPorts() {
  const folders = Object.keys(S.portEdits);
  const problems = [];
  for (const folder of folders) {
    const bench = S.proj.benches.find((b) => b.folder === folder);
    const ports = Object.assign({}, bench.ports, S.portEdits[folder]);
    try {
      const plan = await api('POST', '/api/plan', { folder, ports });
      if (!plan.ok) throw new Error('pre-flight checks failed');
      await api('POST', '/api/run', {});
      delete S.portEdits[folder];
    } catch (err) { problems.push(tilde(folder) + ': ' + err.message); }
  }
  refreshRuns();
  S.projMsg = problems.join(' · ');
  S.projNote = folders.length > problems.length ? `Started ${folders.length - problems.length} port change${folders.length - problems.length === 1 ? '' : 's'}; follow them under Runs. Restart each bench for its new ports to apply, then press Refresh.` : '';
  render();
}

// Which browser this page is in, and its extensions page (pages cannot open that address themselves).
function browserInfo() {
  const brands = ((navigator.userAgentData && navigator.userAgentData.brands) || []).map((b) => b.brand);
  const ua = navigator.userAgent;
  if (navigator.brave) return { name: 'Brave', page: 'brave://extensions' };
  if (brands.includes('Microsoft Edge') || /Edg\//.test(ua)) return { name: 'Microsoft Edge', page: 'edge://extensions' };
  if (brands.includes('Google Chrome')) return { name: 'Google Chrome', page: 'chrome://extensions' };
  if (/Firefox\//.test(ua)) return { name: 'Firefox', page: '' };
  if (brands.includes('Chromium') || /Chrome\//.test(ua)) return { name: 'a Chromium browser', page: 'chrome://extensions' };
  return { name: 'Safari', page: '' };
}

function extensionCard() {
  const head = (badge) => `<div class="row"><h2>Import from Frappe Cloud</h2>${badge}</div>`;
  if (!S.ext) return `<section class="card">${head('<span class="badge">Checking…</span>')}</section>`;
  if (S.ext.connected) {
    return `<section class="card">${head(`<span class="badge green">Extension connected${S.ext.version ? ' · v' + esc(S.ext.version) : ''}</span>`)}
      <p class="lead small">In your Frappe Cloud dashboard, open a site, click the Bench Wizard extension and choose <strong>Send to Bench Wizard</strong>.
      The site's apps, repositories and branches appear under “Paste an app list” below for you to check.</p>
      <div><a class="btn sm" href="https://cloud.frappe.io/dashboard/sites" target="_blank" rel="noopener">Open Frappe Cloud dashboard</a></div></section>`;
  }
  const b = browserInfo();
  const page = b.page || 'chrome://extensions';
  const where = b.page ? `This page is open in ${esc(b.name)}.`
    : `This page is open in ${esc(b.name)}, which cannot load it: use Chrome, Edge or Brave.`;
  return `<section class="card">${head('<span class="badge">Extension not installed</span>')}
    <p class="lead small">A browser extension reads a site's apps, repositories and branches from your Frappe Cloud dashboard and sends them here.
    Install it once, in the browser where you are logged in to Frappe Cloud. ${where}</p>
    <ol class="olist">
      <li>Open <span class="mono">${esc(page)}</span> in a new tab. <button class="btn sm" data-act="copy" data-arg="${esc(page)}">Copy address</button></li>
      <li>Turn on <strong>Developer mode</strong> (top right of that page).</li>
      <li>Click <strong>Load unpacked</strong> and choose the folder <span class="mono">${esc(tilde(S.ext.folder))}</span>.
        <button class="btn sm" data-act="reveal-extension">Show folder</button></li>
    </ol>
    <span class="hint"><span class="spin"></span> Waiting for the extension. This card updates by itself once it is loaded.</span></section>`;
}

// While the Apps step is open: notice the extension, and take any app list it sent.
async function pollExtension() {
  if (S.step !== 'apps' || document.hidden || !S.catalog) return;
  let res;
  try { res = await api('GET', '/api/extension'); } catch (err) { return; }
  const changed = !S.ext || S.ext.connected !== res.connected || S.ext.version !== res.version;
  S.ext = { connected: res.connected, version: res.version, folder: res.folder };
  if (res.inbox) {
    const n = res.inbox.apps.length;
    S.importText = JSON.stringify(res.inbox.apps, null, 2);
    S.importErrors = [];
    S.importMsg = `Received ${n} app${n === 1 ? '' : 's'} from ${res.inbox.site || 'Frappe Cloud'}. Check the list, then click Add apps from list.`;
  }
  if (res.inbox || changed) render();
  if (res.inbox) document.querySelector('[data-bind=importText]').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function viewReview() {
  const back = `<button class="btn" data-act="back">${BACK_LABEL}</button>`;
  if (S.planning) return ['<div class="head"><h1>Review</h1><p class="lead"><span class="spin"></span> Reading each app\'s requirements and running pre-flight checks…</p></div>', back];
  if (!S.plan) return [`<div class="head"><h1>Review</h1></div><div class="note bad">${INFO}<span>${esc(S.planMsg || 'Could not build the plan.')}</span></div>`,
    back + '<div class="right"><button class="btn" data-act="replan">Try again</button></div>'];
  const p = S.plan;
  const order = (p.existing ? [] : ['frappe (' + p.version + ')']).concat(p.apps.map((a) => `${a.name} (${a.branch})`)).join(' → ');
  const added = p.apps.filter((a) => a.added_for).map((a) => `<span class="badge blue">${esc(a.title)} added because ${esc(a.added_for)} needs it</span>`).join(' ');
  const passed = p.checks.filter((c) => c.ok).length;
  const view = `<div class="head"><h1>Review</h1><p class="lead">This is exactly what will run. Go back to change anything; nothing is written until you start.</p></div>
    <section class="card"><div class="row"><h2>Summary</h2><a href="#" data-act="goto" data-arg="project" class="small">Edit project</a></div><div class="kv">
      <div><div class="k">Frappe version</div><div class="v">${esc(p.version_title)} · Python ${esc(p.python)} · Node ${esc(p.node)}</div></div>
      ${p.existing ? `<div><div class="k">Bench</div><div class="v">${p.migrate ? 'Existing; the sites are migrated side by side' : p.restore ? 'Existing; a new site is added from the backup' : 'Existing; apps are added, nothing is removed'}</div></div>`
        : `<div><div class="k">Preset</div><div class="v">${esc(S.catalog.presets[S.preset].title)}</div></div>`}
      <div><div class="k">Bench folder</div><div class="v mono">${esc(tilde(p.folder))}</div></div>
      <div><div class="k">${p.migrate ? 'Sites' : 'Site'}</div><div class="v mono">${esc(p.site)}</div></div>
      ${p.existing && !p.restore ? '' : `<div><div class="k">Database</div><div class="v mono">${S.db ? esc(S.db) : '<span style="color:var(--muted)">chosen by bench</span>'}</div></div>
      <div><div class="k">Site data</div><div class="v">${p.restore ? 'Restored from <span class="mono">' + esc(S.restore.sql) + '</span>' : 'New empty site'}</div></div>`}
      ${p.migrate ? '' : `<div class="full"><div class="k">${p.existing && !p.restore ? 'Apps to add, in install order' : 'Apps, in install order'}</div><div class="v mono">${esc(order)}</div>${added ? `<div class="v">${added}</div>` : ''}</div>`}
    </div></section>
    ${p.notices.length ? `<div class="note info">${INFO}<span>${p.notices.map(esc).join('<br>')}</span></div>` : ''}
    <section class="card"><div class="row"><h2>Pre-flight checks</h2><span class="badge ${p.ok ? 'green' : 'red'}">${passed} of ${p.checks.length} passed</span></div>
      <div class="chk">${p.checks.map((c) => `<div class="crow"><span class="dotc ${c.ok ? 'ok' : 'fail'}">${icon(c.ok ? 'check' : 'priority_high', 16)}</span><span>${esc(c.label)}</span></div>`).join('')}</div></section>
    <section class="card"><div class="row"><h2>Commands</h2><span class="lead small">Secrets shown as ••••</span></div>
      <div class="cmd">${p.steps.map((s, i) => `<span class="n">${i + 1}</span>${esc(s.command)}`).join('\n')}</div>
      <p class="lead small">The bench's own Redis is started for the site steps and stopped when the run ends. Every step after the first runs inside the bench folder with the nvm Node ${esc(p.node)} <span class="mono">bin</span> and <span class="mono">~/.local/bin</span> on PATH.</p></section>`;
  const footer = back + `<div class="right"><button class="btn" data-act="replan">Re-run checks</button>
    <span class="lead small">${p.ok ? '' : 'Disabled while any pre-flight check fails'}${esc(S.runMsg)}</span>
    <button class="btn go" data-act="start" ${p.ok ? '' : 'disabled'}>${icon('play_arrow', 18)}${p.migrate ? 'Start migrate' : 'Start setup'}</button></div>`;
  return [view, footer];
}

const ICON = { done: icon('check', 16), failed: icon('priority_high', 16), running: '<span class="spin"></span>' };

// Why a migrate failed: the patch it was in and what such an error usually means.
const diagnosis = (steps) => steps.filter((s) => s.state === 'failed' && s.diagnosis).map((s) => {
  const d = s.diagnosis;
  return `<div class="note bad diag">${INFO}<div><strong>${esc(s.title)}${d.kind ? ': ' + esc(d.kind) : ' failed'}</strong>${d.patch ? ` in the patch <span class="mono">${esc(d.patch)}</span>` : ''}.
    ${d.hint ? esc(d.hint[0].toUpperCase() + d.hint.slice(1)) + '.' : ''}${d.error ? `<code class="mono">${esc(d.error)}</code>` : ''}</div></div>`;
}).join('');

function viewRun() {
  const r = S.run;
  const steps = r.steps;
  const doneCount = steps.filter((s) => s.state === 'done').length;
  const elapsed = steps.reduce((sum, s) => sum + (s.duration || 0), 0);
  const active = steps.find((s) => s.id === S.activeStep) || steps[0];
  const failedStep = steps.find((s) => s.id === r.failed);
  const pill = { running: ['blue', 'Running'], failed: ['red', 'Run stopped: step failed'], cancelled: ['red', 'Run cancelled'], done: ['green', 'All steps done'] }[r.state] || ['', r.state];
  const list = steps.map((s, i) => `<button class="srow ${s.id === active.id ? 'active' : ''} ${s.state}" data-act="show-step" data-arg="${esc(s.id)}">
    <span class="ic ${s.state}">${ICON[s.state] || i + 1}</span><span class="t">${esc(s.title)}</span><span class="dur">${duration(s.duration)}</span></button>`).join('');
  let fail = '';
  if (r.state === 'failed' && failedStep && r.parallel) {
    const failed = steps.filter((s) => s.state === 'failed');
    fail = `<div class="fail">${INFO}<div class="msg"><strong>${esc(failed.map((s) => s.title).join(', '))} failed.</strong> The steps that finished stay done.</div>
      <button class="btn primary" data-act="retry">Retry failed</button><button class="btn danger" data-act="abandon">Stop here</button></div>`;
  } else if (r.state === 'failed' && failedStep) {
    const n = steps.indexOf(failedStep) + 1;
    const cleanup = !failedStep.cleanup ? '' : failedStep.cleanup_required
      ? `Retrying first runs <span class="mono">${esc(failedStep.cleanup)}</span>, because this step cannot run over a partial result.`
      : `A partial result may be on disk. Cleanup runs <span class="mono">${esc(failedStep.cleanup)}</span> before retrying.`;
    fail = `<div class="fail">${INFO}<div class="msg"><strong>${esc(failedStep.title)} failed.</strong> ${cleanup}</div>
      ${failedStep.cleanup && !failedStep.cleanup_required ? `<label class="tog"><input type="checkbox" data-check="cleanup" ${S.cleanup ? 'checked' : ''}>Run cleanup first</label>` : ''}
      <button class="btn primary" data-act="retry">Retry step ${n}</button><button class="btn danger" data-act="abandon">Stop here</button></div>`;
  } else if (r.state === 'cancelled') {
    fail = r.summary.migrate ? `<div class="fail">${INFO}<div class="msg"><strong>The run was cancelled.</strong> A site stopped in the middle of a migrate may be left in maintenance mode: migrate it again, or run <span class="mono">bench --site SITE set-maintenance-mode off</span>.</div>
      <button class="btn" data-act="restart">Start again</button></div>`
      : r.summary.existing && r.summary.restore ? `<div class="fail">${INFO}<div class="msg"><strong>The run was cancelled.</strong> The site <span class="mono">${esc(r.summary.site)}</span> may be partly created. Remove it with <span class="mono">bench drop-site ${esc(r.summary.site)}</span> before trying again; the rest of the bench is untouched.</div>
      <button class="btn" data-act="restart">Start again</button></div>`
      : r.summary.existing ? `<div class="fail">${INFO}<div class="msg"><strong>The run was cancelled.</strong> Apps fetched or installed so far stay in the bench; running the same plan again skips what is already installed on the site.</div>
      <button class="btn" data-act="restart">Start again</button></div>`
      : `<div class="fail">${INFO}<div class="msg"><strong>The run was cancelled.</strong> What was created so far is still on disk: the bench folder <span class="mono">${esc(tilde(r.summary.folder))}</span>${steps.some((s) => s.id === 'new-site' && s.state !== 'waiting') ? ' and possibly the site ' + esc(r.summary.site) : ''}. Remove them before setting up the same project again.</div>
      <button class="btn" data-act="restart">Set up another bench</button></div>`;
  }
  const title = r.summary.title && !r.summary.migrate ? esc(r.summary.title) + ': ' + esc(tilde(r.summary.folder))
    : r.summary.migrate ? `Migrating ${r.summary.migrate.length === 1 ? esc(r.summary.site) : r.summary.migrate.length + ' sites'}`
    : `${r.summary.existing && !r.summary.restore ? 'Adding apps to' : 'Setting up'} ${esc(r.summary.site)}`;
  const view = `<div class="row" style="align-items:flex-end"><div class="head"><h1>${title}</h1>
      <p class="lead">Run <span class="mono">${esc(r.run_id)}</span> · ${doneCount} of ${steps.length} steps done · ${duration(elapsed)} elapsed</p></div>
      <span class="badge ${pill[0]}"><i class="pip"></i>${esc(pill[1])}</span></div>
    <div class="split"><div class="card flush list"><div class="loghead"><h2>Steps</h2></div>${list}</div>
      <div class="card flush logp"><div class="loghead"><span class="mono c">${esc(active.command)}</span>
        <label class="tog small"><input type="checkbox" data-check="follow" ${S.follow ? 'checked' : ''}>Follow output</label>
        <button class="btn sm" data-act="copy" data-arg="log">Copy log</button></div><pre class="log" id="log"></pre></div></div>${fail}${diagnosis(steps)}`;
  const footer = `<span class="lead small">Logs: <span class="mono">${esc(tilde(r.log_dir))}</span></span><div class="right">
    ${r.state === 'running' ? '<button class="btn danger" data-act="cancel">Cancel run</button>' : ''}
    <button class="btn primary" data-act="finish" ${r.state === 'done' ? '' : 'disabled'}>Finish ${ARROW}</button></div>`;
  return [view, footer];
}

function paintLog() {
  const el = $('log');
  if (!el) return;
  el.textContent = (S.logs[S.activeStep] || []).join('\n');
  if (S.follow) el.scrollTop = el.scrollHeight;
}

const cmdline = (cmd) => `<div class="cmdline"><span class="p">$</span><code>${esc(cmd)}</code><button class="copy" data-act="copy" data-arg="${esc(cmd)}">Copy</button></div>`;

function viewDone() {
  const r = S.run;
  const s = r.summary;
  const folder = tilde(s.folder);
  const total = r.steps.reduce((sum, st) => sum + (st.duration || 0), 0);
  const pw = S.pwFor[r.run_id];
  const footer = `<span class="lead small">Logs: <span class="mono">${esc(tilde(r.log_dir))}</span></span>
    <div class="right"><button class="btn" data-act="restart">Set up another bench</button></div>`;
  if (s.migrate) {
    const longest = Math.max(0, ...r.steps.map((st) => st.duration || 0));
    return [`<div class="hero"><div class="okc">${CHECK_BIG}</div>
      <div><h1>Sites migrated</h1><p>${s.migrate.length} site${s.migrate.length === 1 ? '' : 's'} in <span class="mono">${esc(folder)}</span> migrated side by side in ${duration(longest)}.</p></div></div>
      <section class="card"><h2>Sites</h2><div class="chips">${r.steps.map((st) => `<span class="chip">${esc(st.title.replace(/^migrate /, ''))} <span class="b">${duration(st.duration)}</span></span>`).join('')}</div></section>
      <section class="card"><h2>Start the bench</h2>${cmdline(`cd ${folder} && nvm use && bench start`)}</section>`, footer];
  }
  if (s.op) {
    return [`<div class="hero"><div class="okc">${CHECK_BIG}</div>
      <div><h1>${esc(s.title)}: done</h1><p><span class="mono">${esc(folder)}</span> in ${duration(total)}.</p></div></div>
      ${s.op === 'fc-download' ? `<div class="note info">${INFO}<span>The backup is in <span class="mono">${esc(folder)}/fc-backups</span>. Back on the bench, it is listed under Backups, ready to restore as a new site.</span></div>` : ''}`,
    `<span class="lead small">Logs: <span class="mono">${esc(tilde(r.log_dir))}</span></span><div class="right"><button class="btn primary" data-act="bench-back">${icon('arrow_back', 18)}Back to the bench</button></div>`];
  }
  if (s.title) {
    return [`<div class="hero"><div class="okc">${CHECK_BIG}</div>
      <div><h1>${esc(s.title)}: done</h1><p><span class="mono">${esc(folder)}</span> in ${duration(total)}.</p></div></div>
      ${s.ports ? `<div class="note info">${INFO}<span>The bench uses the new ports the next time it starts. Its sites then open on port <strong>${esc(s.ports.webserver_port)}</strong>, for example <span class="mono">http://localhost:${esc(s.ports.webserver_port)}</span>.</span></div>` : ''}
      <section class="card"><h2>Start the bench</h2>${cmdline(`cd ${folder} && nvm use && bench start`)}</section>`, footer];
  }
  const addOnly = s.existing && !s.restore;
  const port = s.ports ? s.ports.webserver_port : 8000;
  const url = s.existing ? `http://${s.site}:${port}` : `http://localhost:${port}`;
  const restore = !s.restore || !(s.restore.mute_emails || s.restore.pause_scheduler || s.restore.sanitize) ? '' : `<section class="card"><h2>Restored copy: safety switches</h2>
    <div class="note">${INFO}<span>This site was restored from a backup. ${s.restore.mute_emails ? '<strong>Outgoing email is muted.</strong> ' : ''}${s.restore.pause_scheduler ? '<strong>The scheduler is paused.</strong> ' : ''}${s.restore.sanitize ? '<strong>Email that was waiting to be sent is marked Error, and incoming mail, webhooks and SMS or Slack alerts are switched off.</strong> ' : ''}Turn them back on only if this copy should really send email and run jobs.</span></div>
    ${s.restore.mute_emails ? cmdline(`cd ${folder} && bench --site ${s.site} set-config mute_emails 0`) : ''}
    ${s.restore.pause_scheduler ? cmdline(`cd ${folder} && bench --site ${s.site} set-config pause_scheduler 0`) : ''}</section>`;
  const view = `<div class="hero"><div class="okc">${CHECK_BIG}</div>
      <div><h1>Bench ready</h1><p>${esc(s.site)} was ${addOnly ? 'updated' : 'set up'} in ${duration(total)}. ${r.steps.length} steps ran; nothing is running yet.</p></div></div>
    <section class="card"><h2>Start the bench</h2>
      ${S.toolsInstalled ? `<div class="note">${INFO}<span><strong>Open a new terminal first.</strong> Tools were installed in this session and are only on PATH in a fresh shell.</span></div>` : ''}
      ${cmdline(`cd ${folder} && nvm use && bench start`)}
      <p class="lead small"><span class="mono">nvm use</span> reads the <span class="mono">.nvmrc</span> in the bench folder, so Node ${esc(s.node)} is picked whatever the machine default is.</p></section>
    <section class="card"><h2>Site</h2><div class="kv">
      <div><div class="k">URL</div><div class="v"><a href="${esc(url)}" target="_blank" rel="noopener" class="mono">${esc(url)}</a></div></div>
      <div><div class="k">Site name</div><div class="v mono">${esc(s.site)}</div></div>
      <div><div class="k">Login</div><div class="v mono">Administrator</div></div>
      <div><div class="k">Password</div><div class="v mono">${addOnly ? 'unchanged' : pw ? (S.showPw ? esc(pw) : '••••') + ` <a href="#" data-act="toggle-pw" class="small" style="font-family:var(--sans)">${S.showPw ? 'Hide' : 'Show'}</a>` : 'the one you entered in step 1'}</div></div>
      <div><div class="k">Bench folder</div><div class="v mono">${esc(folder)}</div></div>
      <div><div class="k">Developer mode</div><div class="v">${s.existing ? 'unchanged' : 'On'}</div></div></div></section>
    ${restore}
    <section class="card"><h2>${addOnly ? 'Apps added' : 'Installed apps'}</h2><div class="chips">${addOnly ? '' : `<span class="chip">frappe <span class="b">${esc(s.version)}</span></span>`}
      ${s.apps.map((a) => `<span class="chip">${esc(a.name)} <span class="b">${esc(a.branch)}</span></span>`).join('')}</div></section>
    <section class="card"><h2>Update later</h2>${cmdline(`cd ${folder} && nvm use && bench update --pull --build --patch`)}
      ${cmdline(`cd ${tilde(S.catalog.wizard_dir)} && git pull`)}
      <p class="lead small">The first updates the bench and its apps; the second updates Bench Wizard itself, including the shared app catalog.</p></section>`;
  return [view, footer];
}

// ---------------------------------------------------------------- one bench
// The bench page: its sites, apps, backups and logs, read from the bench each time it opens.
const freshBench = (folder) => ({ folder, d: null, busy: 'load', msg: '', note: '', sites: null, checked: false,
  branches: {}, pick: {}, cfg: null, drop: null, migBackup: true, fcDomain: '', fcUrl: 'https://cloud.frappe.io', fc: {},
  log: { name: '', lines: [], offset: null, q: '', errors: false, follow: true } });
const ERROR_LINE = /\b(ERROR|CRITICAL|FATAL)\b|\[(error|crit|alert|emerg)\]|Traceback \(most recent call last\)|^\s*[\w.]*(Error|Exception)\b:/;
const when = (t) => new Date(typeof t === 'number' ? t * 1000 : t).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
const size = (n) => (n >= 1024 ** 3 ? gb(n) : n >= 1024 ** 2 ? (n / 1024 ** 2).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');

function openBench(folder) {
  if (!S.run && S.step !== 'projects' && S.step !== 'bench') S.wizStep = S.step;
  if (S.run) detachRun();
  S.bp = freshBench(folder);
  go('bench');
}

async function loadBenchPage() {
  const bp = S.bp;
  bp.busy = 'load'; bp.msg = ''; render();
  try { bp.d = await api('GET', '/api/bench/detail?folder=' + encodeURIComponent(bp.folder)); } catch (err) { bp.msg = err.message; }
  bp.busy = ''; render();
  if (!bp.d) return;
  if (!bp.log.name && bp.d.logs.length) openLog(bp.d.logs[0].name);
  bp.d.cloud.forEach((domain) => cloudCheck(domain));
  // installed apps per site come from the sites' databases, which is slower: filled in when read
  try { bp.sites = (await api('POST', '/api/bench/inspect', { folder: bp.folder })).sites; } catch (err) { bp.sites = {}; bp.sitesMsg = err.message; }
  if (S.step === 'bench' && S.bp === bp) render();
}

// Plan, then start at once, as the Projects screen does; the run screen shows it, with Back to the bench at the end.
async function benchRun(body, ask) {
  if (ask && !confirm(ask)) return;
  S.bp.busy = 'run'; S.bp.msg = ''; render();
  try {
    const plan = await api('POST', '/api/plan', Object.assign({ folder: S.bp.folder }, body));
    if (!plan.ok) throw new Error(plan.checks.filter((c) => !c.ok).map((c) => c.label).join('; '));
    const res = await api('POST', '/api/run', {});
    S.bp.busy = '';
    attachRun(res.run_id); refreshRuns();
  } catch (err) { S.bp.msg = err.message; S.bp.busy = ''; render(); }
}

async function openLog(name) {
  const log = S.bp.log;
  Object.assign(log, { name, lines: [], offset: null });
  try {
    const res = await api('GET', `/api/bench/log?folder=${encodeURIComponent(S.bp.folder)}&name=${encodeURIComponent(name)}`);
    Object.assign(log, { lines: res.text.split('\n'), offset: res.offset });
  } catch (err) { log.lines = [err.message]; }
  if (S.step === 'bench') render();
}

// Followed logs are polled; only the log box is repainted, so typing elsewhere on the page is not disturbed.
setInterval(async () => {
  const log = S.step === 'bench' && S.bp && S.bp.log;
  if (!log || !log.follow || !log.name || log.offset == null || document.hidden) return;
  try {
    const res = await api('GET', `/api/bench/log?folder=${encodeURIComponent(S.bp.folder)}&name=${encodeURIComponent(log.name)}&offset=${log.offset}`);
    if (!res.text) return;
    const tail = log.lines.pop() + res.text;  // the last line may have been only half written
    log.lines = log.lines.concat(tail.split('\n')).slice(-5000);
    log.offset = res.offset;
    paintBenchLog();
  } catch (err) { /* the bench may be gone; the next tick tries again */ }
}, 2000);

function paintBenchLog() {
  const el = $('bp-log');
  if (!el) return;
  const { lines, q, errors, follow } = S.bp.log;
  const needle = q.trim().toLowerCase();
  const shown = lines.filter((l) => (!needle || l.toLowerCase().includes(needle)) && (!errors || ERROR_LINE.test(l)));
  el.innerHTML = shown.length ? shown.map((l) => (ERROR_LINE.test(l) ? `<span class="eline">${esc(l)}</span>` : esc(l))).join('\n')
    : `<span class="sub">${lines.length > 1 || lines[0] ? 'No line matches.' : 'This log is empty.'}</span>`;
  const count = $('bp-log-count');
  if (count) count.textContent = `${shown.length} of ${lines.length} lines`;
  if (follow) el.scrollTop = el.scrollHeight;
}

async function cloudCheck(domain) {
  const fc = S.bp.fc;
  fc[domain] = Object.assign(fc[domain] || {}, { busy: true });
  if (S.step === 'bench') render();
  try { fc[domain] = await api('GET', '/api/cloud/site?domain=' + encodeURIComponent(domain)); } catch (err) { fc[domain] = { domain, msg: err.message }; }
  if (S.step === 'bench') render();
}

// A backup listed here becomes the "New site from a backup" form of this bench, filled in.
async function restoreAsNewSite(i) {
  const b = S.bp.d.backups[i];
  Object.assign(S, { mode: 'existing', benchFolder: S.bp.folder, wizStep: '' });
  await loadBench();
  if (!S.bench) { S.step = 'project'; return render(); }
  Object.assign(S, { benchNew: true, benchSite: '', benchMigrate: false, siteEdited: true,
    site: b.site.split(/[ .]/)[0].replace(/[^a-z0-9-]/gi, '-').toLowerCase() + '-copy.localhost' });
  Object.assign(S.restore, { sql: b.db, public: b.public, private: b.private });
  go('project');
  inspectBackup();
}

function viewBench() {
  const bp = S.bp;
  const d = bp.d;
  const busy = !!bp.busy;
  const back = `<button class="btn" data-act="projects">${icon('arrow_back', 18)}Projects</button>`;
  if (!d) {
    return [`<div class="head"><h1>${esc(tilde(bp.folder))}</h1></div>
      ${bp.msg ? `<div class="note bad">${INFO}<span>${esc(bp.msg)}</span></div>` : '<p class="lead"><span class="spin"></span> Reading the bench…</p>'}`, back];
  }
  const run = d.running;
  const head = `<div class="row" style="align-items:flex-end"><div class="head"><h1>${esc(tilde(d.folder))}</h1>
      <p class="lead">${esc(d.version_title)} · web on port <span class="mono">${esc(d.ports.webserver_port)}</span> · Node ${esc(d.node || '?')}</p></div>
      ${run == null ? '' : run ? '<span class="badge green"><i class="pip"></i>Running</span>' : '<span class="badge">Stopped</span>'}</div>
    ${bp.msg ? `<div class="note bad">${INFO}<span style="white-space:pre-wrap">${esc(bp.msg)}</span></div>` : ''}
    ${bp.note ? `<div class="note info">${INFO}<span>${esc(bp.note)}</span></div>` : ''}`;

  // Sites: open signed in, back up, edit config, drop; their apps, each removable
  const siteRows = d.sites.map((site) => {
    const info = bp.sites && bp.sites[site];
    const apps = !bp.sites ? '<span class="sub">Reading apps…</span>' : !info ? '' : info.apps
      ? `<div class="chips">${info.apps.map((a) => `<span class="chip path mono">${esc(a)}${a === 'frappe' ? '' : `<button class="chip-x" data-act="bp-uninstall" data-arg="${esc(site + '|' + a)}" aria-label="Uninstall ${esc(a)} from ${esc(site)}" title="Uninstall from this site">${icon('close', 16)}</button>`}</span>`).join('')}</div>`
      : `<span class="sub">${esc(info.error)}</span>`;
    let extra = '';
    if (bp.cfg && bp.cfg.site === site) {
      extra = `<tr class="xrow"><td colspan="3"><label class="f"><span>site_config.json of ${esc(site)}</span>
        <textarea class="in area mono" data-bind="bp.cfg.text" rows="14" spellcheck="false">${esc(bp.cfg.text)}</textarea>
        <span class="hint">Secrets show as ******** and keep their value unless you replace them. Changes apply the next time the site loads its config.</span></label>
        ${bp.cfg.msg ? `<div class="note bad">${INFO}<span>${esc(bp.cfg.msg)}</span></div>` : ''}
        <div class="acts"><button class="btn sm primary" data-act="bp-config-save">${icon('check', 18)}Save</button><button class="btn sm" data-act="bp-config-close">Cancel</button></div></td></tr>`;
    } else if (bp.drop && bp.drop.site === site) {
      extra = `<tr class="xrow"><td colspan="3"><div class="note bad">${INFO}<span><strong>Drop ${esc(site)}?</strong> bench backs it up first and moves its folder to <span class="mono">archived_sites</span>; its database is deleted.</span></div>
        <div class="grid2 end">${field('MariaDB root password', 'bp.drop.pw', { type: 'password', value: bp.drop.pw })}
        <div class="acts"><button class="btn sm danger" data-act="bp-drop-go" ${busy ? 'disabled' : ''}>Drop site</button><button class="btn sm" data-act="bp-drop-close">Cancel</button></div></div></td></tr>`;
    }
    return `<tr><td class="mono">${esc(site)}</td><td>${apps}</td>
      <td class="acts"><button class="btn sm tonal" data-act="bp-login" data-arg="${esc(site)}" ${busy ? 'disabled' : ''} title="${run === false ? 'Starts the bench, then opens the desk signed in as Administrator' : 'Open the desk signed in as Administrator'}">${icon('arrow_forward', 18)}Open as Administrator</button>
        <button class="btn sm" data-act="bp-backup" data-arg="${esc(site)}" ${busy ? 'disabled' : ''}>${icon('storage', 18)}Back up</button>
        <button class="btn sm" data-act="bp-config" data-arg="${esc(site)}" ${busy ? 'disabled' : ''}>${icon('build', 18)}Config</button>
        <button class="btn sm danger" data-act="bp-drop" data-arg="${esc(site)}" ${busy ? 'disabled' : ''}>${icon('close', 18)}Drop</button></td></tr>${extra}`;
  }).join('');
  const sites = `<section class="card flush"><div class="loghead"><h2>Sites</h2><div class="right">
      <label class="tog small"><input type="checkbox" data-check="bp.migBackup" ${bp.migBackup ? 'checked' : ''}>Back up first</label>
      <button class="btn sm" data-act="bp-migrate" ${busy || !d.sites.length ? 'disabled' : ''}>${icon('sync', 18)}Migrate all</button>
      <button class="btn sm" data-act="bp-backup" ${busy || !d.sites.length ? 'disabled' : ''}>${icon('storage', 18)}Back up all</button></div></div>
    <div style="overflow-x:auto"><table class="tbl stack"><thead><tr><th>Site</th><th>Apps installed</th><th></th></tr></thead>
    <tbody>${siteRows || '<tr><td colspan="3" class="sub">This bench has no sites.</td></tr>'}</tbody></table></div></section>`;

  // Apps: branch and commit; after a check, how far behind; update, or move to another branch
  const appRows = d.apps.map((a) => {
    const status = a.error ? `<span class="badge red">${esc(a.error)}</span>`
      : !bp.checked ? (a.changed ? '<span class="badge amber">Local changes</span>' : '')
        : `${a.behind ? `<span class="badge blue">${a.behind} new commit${a.behind === 1 ? '' : 's'}</span>` : a.behind === 0 ? '<span class="badge green">Up to date</span>' : ''}${a.changed ? ' <span class="badge amber">Local changes</span>' : ''}`;
    const list = bp.branches[a.name];
    const switcher = list ? `<select class="in sm" data-bind="bp.pick.${esc(a.name)}" aria-label="Branch for ${esc(a.name)}">${list.map((b) => `<option ${b === bp.pick[a.name] ? 'selected' : ''}>${esc(b)}</option>`).join('')}</select>
        <button class="btn sm" data-act="bp-switch" data-arg="${esc(a.name)}" ${busy ? 'disabled' : ''}>Switch</button>`
      : `<button class="btn sm" data-act="bp-branches" data-arg="${esc(a.name)}" ${busy || a.error || !a.remote ? 'disabled' : ''}>Change branch</button>`;
    return `<tr><td class="mono">${esc(a.name)}</td><td class="mono">${esc(a.branch)}</td>
      <td class="mono">${esc(a.commit)} <span class="sub">${esc(a.date)}</span></td><td>${status}</td>
      <td class="acts">${a.behind && bp.checked ? `<button class="btn sm tonal" data-act="bp-update" data-arg="${esc(a.name)}" ${busy ? 'disabled' : ''}>${icon('sync', 18)}Update</button>` : ''}${switcher}</td></tr>`;
  }).join('');
  const apps = `<section class="card flush"><div class="loghead"><h2>Apps</h2><div class="right">
      <button class="btn sm" data-act="bp-check" ${busy ? 'disabled' : ''}>${bp.busy === 'check' ? '<span class="spin"></span> Checking…' : icon('refresh', 18) + 'Check for updates'}</button></div></div>
    <div style="overflow-x:auto"><table class="tbl stack"><thead><tr><th>App</th><th>Branch</th><th>Commit</th><th>Status</th><th></th></tr></thead><tbody>${appRows}</tbody></table></div>
    <p class="lead small" style="padding:0 16px 16px">Update and Change branch pull the app, then run <span class="mono">bench setup requirements</span>, migrate every site and build the app's assets.</p></section>`;

  // Backups: on disk, and Frappe Cloud's; any of them becomes a new site
  const backupRows = d.backups.map((b, i) => `<tr><td class="mono">${esc(b.site)}</td><td>${esc(when(b.when))}</td>
      <td><div class="chips">${[['db', 'Database'], ['public', 'Public files'], ['private', 'Private files']].filter(([k]) => b[k]).map(([, l]) => `<span class="chip">${l}</span>`).join('')}</div></td>
      <td>${size(b.size)}</td><td class="acts"><button class="btn sm" data-act="bp-restore" data-arg="${i}" ${busy ? 'disabled' : ''}>${icon('add', 18)}Restore as a new site</button></td></tr>`).join('');
  const cloud = d.cloud.map((domain) => {
    const c = bp.fc[domain] || {};
    const remove = `<button class="btn sm" data-act="bp-fc-remove" data-arg="${esc(domain)}">Remove</button>`;
    let body;
    if (c.busy) body = '<span class="sub"><span class="spin"></span> Asking Frappe Cloud…</span>';
    else if (c.msg) body = `<span class="err">${esc(c.msg)}</span> <button class="btn sm" data-act="bp-fc-check" data-arg="${esc(domain)}">Check again</button> ${remove}`;
    else if (c.status === 'Approved') {
      body = `<span class="badge green">Approved</span> ${c.running ? '<span class="badge blue">A backup is being taken</span>' : ''}
        <table class="tbl stack"><tbody>${c.backups.map((b) => `<tr><td class="mono">${esc(b.name)}</td><td>${b.when ? esc(when(b.when)) : ''}</td><td>${b.size ? size(b.size) : ''}</td>
          <td class="acts"><button class="btn sm tonal" data-act="bp-fc-download" data-arg="${esc(domain + '|' + b.name)}" ${busy ? 'disabled' : ''}>Download into this bench</button></td></tr>`).join('') || '<tr><td class="sub">No backups yet.</td></tr>'}</tbody></table>
        <div class="acts"><button class="btn sm" data-act="bp-fc-take" data-arg="${esc(domain)}">Take a new backup</button><button class="btn sm" data-act="bp-fc-check" data-arg="${esc(domain)}">${icon('refresh', 18)}Refresh</button>${remove}</div>`;
    } else {
      body = `<span class="badge amber">${esc(c.status || 'Pending')}</span>
        <p class="lead small">The site's owner approves this on Frappe Cloud${c.approval_url ? ` (<a href="${esc(c.approval_url)}" target="_blank" rel="noopener">open the approval page</a>)` : ''}, checking that it shows the code <strong class="mono">${esc(c.code)}</strong>.</p>
        <div class="acts"><button class="btn sm" data-act="bp-fc-check" data-arg="${esc(domain)}">${icon('refresh', 18)}Check again</button>${remove}</div>`;
    }
    const via = c.url && c.url !== 'https://cloud.frappe.io' ? ` <span class="via">on ${esc(c.url.replace('https://', ''))}</span>` : '';
    return `<div class="fcsite"><h3><span class="mono">${esc(domain)}</span>${via}</h3>${body}</div>`;
  }).join('');
  const backups = `<section class="card flush"><div class="loghead"><h2>Backups</h2></div>
    ${backupRows ? `<div style="overflow-x:auto"><table class="tbl stack"><thead><tr><th>Site</th><th>Taken</th><th>Files</th><th>Size</th><th></th></tr></thead><tbody>${backupRows}</tbody></table></div>`
    : '<p class="lead small" style="padding:0 16px">No backups in this bench yet. Back up a site above to make one.</p>'}
    <p class="lead small" style="padding:8px 16px 0">To copy a site, back it up, then restore that backup as a new site. The new site starts with outgoing email muted and the scheduler paused.</p>
    <div class="fc"><h3>From Frappe Cloud</h3>
      <p class="lead small">Ask for access to a site on Frappe Cloud. Once its owner approves, its backups can be downloaded into this bench and restored here.</p>
      <div class="grid2">${field('Frappe Cloud site', 'bp.fcDomain', { mono: true, value: bp.fcDomain, placeholder: 'mysite.frappe.cloud' })}
      ${field('Frappe Cloud address', 'bp.fcUrl', { mono: true, value: bp.fcUrl, placeholder: 'https://cloud.frappe.io', hint: 'Change it only for an organisation that runs its own Frappe Cloud (Press)' })}</div>
      <div class="acts"><button class="btn" data-act="bp-fc-connect" ${busy ? 'disabled' : ''}>Ask for access</button></div>${cloud}</div></section>`;

  // Logs: pick a file, search it, keep following it
  const log = bp.log;
  const logs = `<section class="card flush"><div class="loghead"><h2>Logs</h2>
      ${d.logs.length ? `<div class="right logtools"><select class="in sm" data-bplog aria-label="Log file">${d.logs.map((l) => `<option value="${esc(l.name)}" ${l.name === log.name ? 'selected' : ''}>${esc(l.name)} · ${size(l.size)}</option>`).join('')}</select>
      <input class="in sm" data-bind="bp.log.q" value="${esc(log.q)}" placeholder="Search" aria-label="Search the log">
      <label class="tog small"><input type="checkbox" data-check="bp.log.errors" ${log.errors ? 'checked' : ''}>Errors only</label>
      <label class="tog small"><input type="checkbox" data-check="bp.log.follow" ${log.follow ? 'checked' : ''}>Follow</label>
      <span class="lead small" id="bp-log-count"></span></div>` : ''}</div>
    ${d.logs.length ? '<pre class="log" id="bp-log"></pre>' : '<p class="lead small" style="padding:0 16px 16px">This bench has written no logs yet.</p>'}</section>`;

  const footer = `${back}<div class="right">${run === true ? `<button class="btn stop" data-act="bp-power" data-arg="stop" ${busy ? 'disabled' : ''}>${icon('stop', 18)}Stop</button>`
    : run === false ? `<button class="btn tonal" data-act="bp-power" data-arg="start" ${busy ? 'disabled' : ''}>${icon('play_arrow', 18)}Start</button>` : ''}
    <button class="btn" data-act="bp-refresh" ${busy ? 'disabled' : ''}>${icon('refresh', 18)}Refresh</button></div>`;
  return [head + sites + apps + backups + logs, footer];
}

const BENCH_ACTIONS = {
  'bench-open': (arg) => openBench(arg),
  'bench-back': async () => {
    const id = S.run && S.run.run_id;
    const folder = S.run.summary.folder;
    detachRun();
    try { await api('POST', '/api/run/dismiss', { run: id }); } catch (err) { /* a running run stays */ }
    refreshRuns(); openBench(folder);
  },
  'bp-refresh': () => { S.bp.note = ''; loadBenchPage(); },
  'bp-power': async (what) => {
    S.bp.busy = what; S.bp.msg = ''; S.bp.note = `${what === 'start' ? 'Starting' : 'Stopping'} the bench…`; render();
    try {
      const res = await api('POST', '/api/bench/' + what, { folder: S.bp.folder });
      S.bp.note = what === 'stop' ? 'The bench stopped.' : `The bench is running, web on port ${res.port}. It keeps running after you close the wizard; its output is in ${tilde(res.log)}.`;
    } catch (err) { S.bp.note = ''; S.bp.msg = err.message; }
    loadBenchPage();
  },
  'bp-login': async (site) => {
    // A stopped bench is started first: the sign-in lives in its Redis and its web server serves the desk.
    const starting = S.bp.d.running === false;
    const wait = starting ? 'Starting the bench and signing in…' : 'Signing in…';
    const tab = window.open('', '_blank');  // opened now, while the click still counts, or a pop-up blocker stops it
    try { tab.document.title = wait; tab.document.body.textContent = wait; tab.document.body.style.font = '16px system-ui, sans-serif'; } catch (e) { /* no tab: fine */ }
    S.bp.busy = 'login'; S.bp.msg = ''; S.bp.note = starting ? `Starting the bench, then signing in to ${site}. This can take up to a minute.` : ''; render();
    let started = false;
    try {
      const res = await api('POST', '/api/site/login', { folder: S.bp.folder, site });
      started = res.started;
      S.bp.note = started ? `The bench is running now, web on port ${S.bp.d.ports.webserver_port}; ${site} opened signed in as Administrator.` : '';
      if (tab) { tab.opener = null; tab.location = res.url; } else window.location.assign(res.url);
    } catch (err) { if (tab) tab.close(); S.bp.note = ''; S.bp.msg = `${site}: ${err.message}`; }
    S.bp.busy = '';
    if (started) loadBenchPage(); else render();
  },
  'bp-backup': (site) => benchRun({ op: 'backup', sites: site ? [site] : undefined }),
  'bp-migrate': () => benchRun({ migrate: true, sites: S.bp.d.sites, backup: S.bp.migBackup }),
  'bp-config': async (site) => {
    try { S.bp.cfg = { site, text: (await api('GET', `/api/site/config?folder=${encodeURIComponent(S.bp.folder)}&site=${encodeURIComponent(site)}`)).text, msg: '' }; S.bp.drop = null; }
    catch (err) { S.bp.msg = err.message; }
    render();
  },
  'bp-config-save': async () => {
    const cfg = S.bp.cfg;
    try { await api('POST', '/api/site/config', { folder: S.bp.folder, site: cfg.site, text: cfg.text }); S.bp.cfg = null; S.bp.note = `Saved site_config.json of ${cfg.site}.`; }
    catch (err) { cfg.msg = err.message; }
    render();
  },
  'bp-config-close': () => { S.bp.cfg = null; render(); },
  'bp-drop': (site) => { S.bp.drop = { site, pw: S.rootPw || '' }; S.bp.cfg = null; render(); },
  'bp-drop-close': () => { S.bp.drop = null; render(); },
  'bp-drop-go': () => benchRun({ op: 'drop', site: S.bp.drop.site, root_password: S.bp.drop.pw }),
  'bp-uninstall': (arg) => {
    const [site, app] = arg.split('|');
    benchRun({ op: 'uninstall', site, app }, `Uninstall ${app} from ${site}? Its doctypes and their data are deleted from this site; bench backs the site up first. The app stays in the bench.`);
  },
  'bp-check': async () => {
    S.bp.busy = 'check'; render();
    try { S.bp.d.apps = (await api('POST', '/api/bench/updates', { folder: S.bp.folder })).apps; S.bp.checked = true; } catch (err) { S.bp.msg = err.message; }
    S.bp.busy = ''; render();
  },
  'bp-update': (app) => benchRun({ op: 'update-app', app }, `Pull ${app}'s new commits, then set up requirements, migrate every site of this bench and build ${app}?`),
  'bp-branches': async (app) => {
    S.bp.busy = 'branches'; render();
    try { const list = (await api('POST', '/api/bench/branches', { folder: S.bp.folder, app })).branches; S.bp.branches[app] = list; S.bp.pick[app] = list[0]; }
    catch (err) { S.bp.msg = `${app}: ${err.message}`; }
    S.bp.busy = ''; render();
  },
  'bp-switch': (app) => {
    const branch = S.bp.pick[app];
    const current = S.bp.d.apps.find((a) => a.name === app).branch;
    if (branch === current) { S.bp.msg = `${app} is on ${branch} already; use Check for updates to pull new commits.`; return render(); }
    benchRun({ op: 'update-app', app, branch }, `Move ${app} from ${current} to ${branch}, then set up requirements, migrate every site of this bench and build ${app}? Moving to another major version changes every site's data; back the sites up first.`);
  },
  'bp-restore': (i) => restoreAsNewSite(Number(i)),
  'bp-fc-connect': async () => {
    const domain = S.bp.fcDomain.trim().toLowerCase();
    S.bp.busy = 'fc'; S.bp.msg = ''; render();
    try { const res = await api('POST', '/api/cloud/connect', { domain, url: S.bp.fcUrl.trim() }); S.bp.fc[res.domain] = res; if (!S.bp.d.cloud.includes(res.domain)) S.bp.d.cloud.push(res.domain); S.bp.fcDomain = ''; }
    catch (err) { S.bp.msg = err.message; }
    S.bp.busy = ''; render();
  },
  'bp-fc-check': (domain) => cloudCheck(domain),
  'bp-fc-take': async (domain) => {
    try { await api('POST', '/api/cloud/backup', { domain }); S.bp.note = `Frappe Cloud is taking a backup of ${domain}. Refresh in a few minutes to see it.`; }
    catch (err) { S.bp.msg = err.message; }
    cloudCheck(domain);
  },
  'bp-fc-download': (arg) => { const [domain, backup] = arg.split('|'); benchRun({ op: 'fc-download', domain, backup }); },
  'bp-fc-remove': async (domain) => {
    try { await api('POST', '/api/cloud/disconnect', { domain }); } catch (err) { /* forgotten either way */ }
    S.bp.d.cloud = S.bp.d.cloud.filter((x) => x !== domain); delete S.bp.fc[domain]; render();
  },
};

const VIEWS = { project: viewProject, check: viewCheck, apps: viewApps, review: viewReview, run: viewRun, done: viewDone, projects: viewProjects, bench: viewBench };

// ---------------------------------------------------------------- actions
function validateProject() {
  const e = {};
  if (S.mode === 'existing' && S.benchNew) {
    const site = S.site.trim();
    if (!/^[a-z0-9][a-z0-9.-]*$/.test(site)) e.site = 'Use lowercase letters, digits, dots and hyphens';
    else if (S.bench.sites[site]) e.site = 'This bench already has a site with this name';
    if (S.db && !/^[A-Za-z0-9_]+$/.test(S.db)) e.db = 'Use letters, digits and underscores';
    if (!S.pw) e.pw = 'Enter a password for the Administrator user';
    if (!S.rootPw) e.rootPw = 'Enter the MariaDB root password';
    if (!S.restore.sql.trim()) e['restore.sql'] = 'Enter the path to the database backup';
    S.errors = e;
    return !Object.keys(e).length;
  }
  if (S.mode === 'existing') {
    if (!S.bench || !(S.benchSite || (S.benchMigrate && migrateList().length))) e.benchSite = 'Load the bench and pick a site';
    S.errors = e;
    return !Object.keys(e).length;
  }
  if (!S.folder.trim()) e.folder = 'Enter a folder for the new bench';
  else if (/\s/.test(S.folder.trim())) e.folder = 'The path must not contain spaces; bench cannot work in such a folder';
  if (!/^[a-z0-9][a-z0-9.-]*$/.test(S.site.trim())) e.site = 'Use lowercase letters, digits, dots and hyphens';
  if (S.db && !/^[A-Za-z0-9_]+$/.test(S.db)) e.db = 'Use letters, digits and underscores';
  if (!S.pw) e.pw = 'Enter a password for the Administrator user';
  if (S.siteData === 'restore' && !S.restore.sql.trim()) e['restore.sql'] = 'Enter the path to the database backup';
  S.errors = e;
  return !Object.keys(e).length;
}

async function loadChecks(force) {
  if (!force && S.checks && S.checksVersion === S.version) return;
  S.checking = true; S.checks = S.checksVersion === S.version ? S.checks : null; render();
  try {
    const res = await api('GET', '/api/checks?version=' + encodeURIComponent(S.version));
    S.checks = res.rows; S.mac = !!res.mac;
    S.checksVersion = S.version;
  } catch (err) { S.installMsg = err.message; }
  S.checking = false; render();
}

async function installMissing() {
  S.installMsg = '';
  for (const row of S.checks.filter((r) => r.status !== 'ok')) {
    S.installing = row.id; render();
    let res;
    try { res = await api('POST', '/api/checks/install', { id: row.id, version: S.version, sudo_password: S.sudo }); }
    catch (err) { S.installMsg = err.message; break; }
    if (res.rows) S.checks = res.rows;
    if (res.log) S.installLogs[row.id] = res.log;
    if (res.error === 'wrong_sudo_password') { S.installMsg = 'sudo did not accept that password. Check it and try again.'; break; }
    if (!res.ok) { S.installMsg = row.title + ' could not be installed. Its log is shown in the table.'; break; }
    S.toolsInstalled = true;
  }
  S.installing = ''; render();
}

async function verifyRoot() {
  S.rootBusy = true; S.rootMsg = ''; render();
  try { S.rootOk = (await api('POST', '/api/mariadb/verify', { password: S.rootPw })).ok; }
  catch (err) { S.rootOk = false; S.rootMsg = err.message; }
  S.rootBusy = false; render();
}

async function setRoot() {
  if (!S.rootPw) { S.rootMsg = 'Enter the password to set first.'; return render(); }
  if (!S.sudo) {
    const typed = window.prompt('Setting the MariaDB root password needs your sudo password:');
    if (!typed) return;
    S.sudo = typed;
  }
  if (!window.confirm('Set the MariaDB root password on this machine to the one you entered?')) return;
  S.rootBusy = true; S.rootMsg = ''; render();
  try {
    const res = await api('POST', '/api/mariadb/set-password', { sudo_password: S.sudo, password: S.rootPw });
    if (res.ok) S.rootOk = true;
    else S.rootMsg = res.error === 'wrong_sudo_password' ? 'sudo did not accept that password.' : 'MariaDB refused the change. ' + (res.log || '');
    if (res.error === 'wrong_sudo_password') S.sudo = '';
  } catch (err) { S.rootMsg = err.message; }
  S.rootBusy = false; render();
}

async function inspectBackup() {
  const path = S.restore.sql.trim();
  if (!path || !restoring()) return;
  S.backupMsg = '';
  try {
    const res = await api('POST', '/api/backup/inspect', { path });
    if (res.ok) { S.backup = res; S.backupPath = S.restore.sql; } else { S.backup = null; S.backupMsg = res.error; }
  } catch (err) { S.backupMsg = err.message; }
  if (S.step === 'project') render();
}

async function loadBench() {
  const folder = S.benchFolder.trim();
  if (!folder) { S.benchMsg = 'Enter the bench folder'; return render(); }
  S.benchBusy = true; S.benchMsg = ''; render();
  try {
    S.bench = await api('POST', '/api/bench/inspect', { folder });
    S.version = S.bench.version;
    S.catalog.frappe_versions[S.version] = S.bench.profile;  // a version the catalog lacks: read from the bench
    const readable = Object.keys(S.bench.sites).filter((n) => S.bench.sites[n].apps);
    S.benchSite = readable.length === 1 ? readable[0] : '';
    S.benchNew = false; S.benchMigrate = false;
    S.migrateSites = Object.fromEntries(Object.keys(S.bench.sites).map((n) => [n, true]));
  } catch (err) { S.bench = null; S.benchSite = ''; S.benchMsg = err.message; }
  S.benchBusy = false; render();
}

function mergeApp(app) {
  // An app read from its repository: a new custom card, or new repo/branch for a known one.
  const old = S.apps[app.name] || {};
  S.apps[app.name] = Object.assign({}, old, {
    title: old.title && !old.custom ? old.title : app.title, description: app.description || old.description || '',
    repo: app.repo, branch: app.branch, requires: app.requires, custom: !old.title || !!old.custom,
    available: true, selected: true, credentials: !!app.credentials, private: !!old.private, local: !!old.local,
    locked: false, inBackup: !!old.inBackup, fromPreset: !!old.fromPreset,
  });
  selectRequired(app.name);
}

async function addCustom() {
  const repo = S.custom.repo.trim();
  const branch = S.custom.branch.trim() || S.version;
  if (!repo) { S.customMsg = 'Enter the Git URL of the app'; return render(); }
  S.customBusy = true; S.customMsg = ''; render();
  try {
    const res = await api('POST', '/api/apps/inspect', { repo, branch });
    if (!res.ok) S.customMsg = res.error;
    else if (res.app.name === 'frappe') S.customMsg = 'Frappe itself comes from the version you chose';
    else { mergeApp(res.app); S.custom = { repo: '', branch: '' }; }
  } catch (err) { S.customMsg = err.message; }
  S.customBusy = false; render();
}

async function importApps() {
  let list;
  try { list = JSON.parse(S.importText); } catch (err) { list = null; }
  if (!Array.isArray(list) || !list.length || !list.every((i) => i && typeof i.url === 'string' && i.url)) {
    S.importErrors = []; S.importMsg = ''; S.errors = { importText: 'Paste a JSON list of {"url": …, "branch": …}' };
    S.importErrors = [{ repo: 'list', branch: '-', error: 'This is not a JSON list of {"url": …, "branch": …}' }];
    S.importMsg = 'Nothing was added.'; S.errors = {};
    return render();
  }
  S.importBusy = true; S.importMsg = ''; S.importErrors = []; render();
  try {
    const res = await api('POST', '/api/apps/import', { apps: list, version: S.version });
    const names = [];
    for (const app of res.apps) {
      if (!app.ok) S.importErrors.push(app);
      else if (app.name !== 'frappe') { mergeApp(app); names.push(app.name); }
    }
    // The list's order is the install order for apps that declare no requirements.
    const ordered = {};
    for (const name of ['frappe'].concat(names, Object.keys(S.apps))) if (S.apps[name]) ordered[name] = S.apps[name];
    S.apps = ordered;
    S.importMsg = `${names.length} of ${res.apps.length} apps added.`;
    S.importText = '';  // it may have held a token
  } catch (err) { S.importErrors = [{ repo: 'list', branch: '-', error: err.message }]; S.importMsg = 'Nothing was added.'; }
  S.importBusy = false; render();
}

function toggleApp(name, on) {
  const app = S.apps[name];
  S.depWarn = null;
  if (on) { app.selected = true; selectRequired(name); }
  else {
    const others = dependants(name);
    if (others.length) S.depWarn = { app: name, others };
    else app.selected = false;
  }
  render();
}

async function buildPlan() {
  S.planning = true; S.plan = null; S.planMsg = ''; S.runMsg = ''; render();
  const apps = selectedApps().filter(([n]) => n !== 'frappe')
    .map(([name, a]) => ({ name, repo: a.repo, branch: a.branch, title: a.title }));
  const body = isMigrate() ? { migrate: true, folder: S.bench.folder, sites: migrateList(), backup: S.migrateBackup }
    : isExisting() && S.benchNew ? {
    existing: true, folder: S.bench.folder, site: S.site.trim(), db_name: S.db.trim(), admin_password: S.pw,
    mariadb_root_password: S.rootPw, apps, restore: S.restore,
  } : isExisting() ? { existing: true, folder: S.bench.folder, site: S.benchSite, apps } : {
    version: S.version, folder: S.folder.trim(), site: S.site.trim(), db_name: S.db.trim(), admin_password: S.pw,
    mariadb_root_password: S.rootPw, apps, restore: S.siteData === 'restore' ? S.restore : null,
  };
  try { S.plan = await api('POST', '/api/plan', body); } catch (err) { S.planMsg = err.message; }
  S.planning = false; render();
}

async function startRun() {
  let res;
  try { res = await api('POST', '/api/run', {}); } catch (err) { S.runMsg = ' ' + err.message; return render(); }
  S.pwFor[res.run_id] = S.pw;
  resetWizard();
  attachRun(res.run_id);
  refreshRuns();
}

// The wizard's answers go back to a fresh start, so the next setup can be planned while this one runs.
function startOver() {
  if (!confirm('Clear every choice made so far and go back to the Project step? Nothing on disk changes.')) return;
  const keep = Object.fromEntries(KEEP.map((k) => [k, S[k]]));
  Object.assign(S, JSON.parse(FRESH), keep, { version: S.catalog.default_version });
  applyPreset(S.catalog.default_preset);
  go('project');
}

function resetWizard() {
  Object.assign(S, { plan: null, reached: 0, folderEdited: false, siteEdited: false, appsKey: '', showPw: false, bench: null, benchSite: '', benchNew: false, benchMigrate: false, wizStep: '' });
  applyPreset(S.preset);
}

let source = null;

// Show one run: its steps and logs come from its own event stream.
function attachRun(id) {
  if (source) source.close();
  Object.assign(S, { run: null, logs: {}, follow: true, showPw: false, attaching: true });
  source = new EventSource('/api/run/events?token=' + encodeURIComponent(TOKEN) + '&run=' + encodeURIComponent(id));
  source.onmessage = (msg) => onEvent(JSON.parse(msg.data));
}

function detachRun() {
  if (source) source.close();
  source = null;
  S.run = null;
}

const runQuery = () => 'run=' + encodeURIComponent(S.run.run_id);

async function loadLog(id) {
  const run = S.run;
  let lines;
  try { lines = (await api('GET', '/api/run/log?' + runQuery() + '&step=' + encodeURIComponent(id))).log.split('\n').filter((l, i, a) => i < a.length - 1 || l); }
  catch (err) { lines = S.logs[id] || []; }
  if (S.run !== run) return;  // another run was opened meanwhile
  S.logs[id] = lines;
  if (S.activeStep === id) paintLog();
}

function onEvent(ev) {
  if (ev.type === 'snapshot') {
    if (ev.state === 'none') return;
    S.run = ev;
    const focus = ev.steps.find((s) => s.state === 'running') || ev.steps.find((s) => s.id === ev.failed)
      || ev.steps.filter((s) => s.state === 'done').pop() || ev.steps[0];
    S.activeStep = focus.id;
    // A run just opened shows its Done screen when finished; a reconnect keeps the screen that is up.
    if (S.attaching) S.step = ev.state === 'done' ? 'done' : 'run';
    else if (S.step !== 'done') S.step = 'run';
    S.attaching = false;
    render();
    loadLog(focus.id);
  } else if (!S.run) {
    return;
  } else if (ev.type === 'log') {
    (S.logs[ev.id] = S.logs[ev.id] || []).push(ev.line);
    if (ev.id === S.activeStep && S.step === 'run') {
      const el = $('log');
      if (el) { el.textContent += (el.textContent ? '\n' : '') + ev.line; if (S.follow) el.scrollTop = el.scrollHeight; }
    }
  } else if (ev.type === 'step') {
    const step = S.run.steps.find((s) => s.id === ev.id);
    step.state = ev.state; step.duration = ev.duration; step.diagnosis = ev.diagnosis;
    if (ev.state === 'running') { S.logs[ev.id] = []; if (S.follow) S.activeStep = ev.id; }
    if (ev.state === 'failed') { S.run.failed = ev.id; S.activeStep = ev.id; }
    if (S.step === 'run') render();
  } else if (ev.type === 'run') {
    S.run.state = ev.state;
    if (ev.state === 'running') S.run.failed = null;
    if (S.step === 'run') render();
  }
}

function setPath(path, value) {
  const parts = path.split('.');
  let target = S;
  while (parts.length > 1) target = target[parts.shift()];
  target[parts[0]] = value;
}

function go(step) {
  if (isMigrate() && (step === 'apps' || step === 'check')) step = 'project';
  S.step = step; S.errors = {}; render(); window.scrollTo(0, 0);
  if (step === 'check') loadChecks(false);
  if (step === 'review') buildPlan();
  if (step === 'apps') { pollExtension(); loadMarket(); }
  if (step === 'projects') loadProjects();
  if (step === 'bench') loadBenchPage();
}

const NEXT = { project: 'check', check: 'apps', apps: 'review' };
const BACK = { check: 'project', apps: 'check', review: 'apps' };

const ACTIONS = {
  goto: (arg) => go(arg),
  back: () => go(S.step === 'apps' && S.mode === 'existing' ? 'project' : BACK[S.step]),
  next: () => {
    if (S.step === 'project' && !validateProject()) return render();
    if (S.step === 'apps' && S.depWarn) return;
    go(S.step === 'project' && S.mode === 'existing' ? (S.benchMigrate ? 'review' : 'apps') : NEXT[S.step]);
  },
  version: (arg) => { pickVersion(arg); render(); },
  mode: (arg) => {
    S.mode = arg; S.errors = {};
    // the loaded bench decides the version; a new bench starts from the default again
    S.version = arg === 'existing' && S.bench ? S.bench.version : S.catalog.default_version;
    render();
  },
  'load-bench': loadBench,
  'bench-site': (arg) => { S.benchSite = arg; S.benchNew = false; S.benchMigrate = false; S.errors = {}; render(); },
  'bench-new': () => { S.benchNew = true; S.benchSite = ''; S.benchMigrate = false; S.errors = {}; render(); inspectBackup(); },
  'bench-migrate': () => { S.benchMigrate = true; S.benchNew = false; S.benchSite = ''; S.errors = {}; render(); },
  preset: (arg) => { applyPreset(arg); render(); },
  sitedata: (arg) => { S.siteData = arg; render(); if (arg === 'restore') inspectBackup(); },
  recheck: () => loadChecks(true),
  install: installMissing,
  'verify-root': verifyRoot,
  'set-root': setRoot,
  'add-custom': addCustom,
  import: importApps,
  'remove-custom': (arg) => { delete S.apps[arg]; S.depWarn = null; render(); },
  'dep-all': () => { for (const n of S.depWarn.others.concat(S.depWarn.app)) S.apps[n].selected = false; S.depWarn = null; render(); },
  'dep-keep': () => { S.depWarn = null; render(); },
  replan: buildPlan,
  start: startRun,
  'show-step': (arg) => { S.activeStep = arg; S.follow = false; render(); if (!S.logs[arg]) loadLog(arg); },
  retry: async () => { S.follow = true; try { await api('POST', '/api/run/retry', { run: S.run.run_id, cleanup: S.cleanup }); } catch (err) { window.alert(err.message); } },
  cancel: async () => {
    if (!window.confirm('Cancel the run? The current step is stopped. Whatever was created so far stays on disk.')) return;
    try { await api('POST', '/api/run/cancel', { run: S.run.run_id }); } catch (err) { window.alert(err.message); }
  },
  abandon: () => { S.run.state = 'cancelled'; render(); },
  finish: () => { S.step = 'done'; render(); window.scrollTo(0, 0); },
  restart: async () => {
    const id = S.run && S.run.run_id;
    detachRun();
    try { await api('POST', '/api/run/dismiss', { run: id }); } catch (err) { /* a running run stays */ }
    resetWizard(); S.step = 'project'; render(); refreshRuns();
  },
  'open-run': (arg) => { if (!S.run && S.step !== 'projects' && S.step !== 'bench') S.wizStep = S.step; attachRun(arg); },
  projects: () => {
    if (S.run) detachRun(); else if (S.step !== 'projects' && S.step !== 'bench') S.wizStep = S.step;
    go('projects');
  },
  'proj-migrate': (arg) => {
    const bench = S.proj.benches.find((b) => b.folder === arg);
    startQuick({ migrate: true, folder: arg, sites: S.projSite[arg] ? [S.projSite[arg]] : bench.sites }, arg);
  },
  'proj-build': (arg) => startQuick({ build: true, folder: arg }, arg),
  'proj-filter': (arg) => { S.projFilter = arg; render(); },
  'fx-open': () => browseTo(S.addFolder.trim()),
  'fx-go': (arg) => browseTo(arg),
  'fx-close': () => { S.fx = null; render(); },
  'fx-add': async (arg) => {
    try {
      const res = await api('POST', '/api/projects/add', { folder: arg });
      S.fx = null;
      S.projNote = res.kind === 'bench' ? `Added the bench ${tilde(res.folder)}.` : `Benches in ${tilde(res.folder)} are listed from now on.`;
      loadProjects();
    } catch (err) { S.fx.msg = err.message; render(); }
  },
  'proj-remove': async (arg) => {
    try { await api('POST', '/api/projects/remove', { folder: arg }); loadProjects(); }
    catch (err) { S.projMsg = err.message; render(); }
  },
  'proj-start': (arg) => benchPower(arg, 'start'),
  'proj-stop': (arg) => benchPower(arg, 'stop'),
  'ports-auto': async () => {
    try {
      const moves = (await api('POST', '/api/ports/allocate', {})).moves;
      for (const [folder, ports] of Object.entries(moves)) S.portEdits[folder] = ports;
      const n = Object.keys(moves).length;
      S.projNote = n ? `New ports for ${n} bench${n === 1 ? '' : 'es'} are filled in below; running benches keep theirs. Check them, then Apply.` : 'No bench needs new ports.';
    } catch (err) { S.projMsg = err.message; }
    render();
  },
  'ports-apply': applyPorts,
  'proj-add': async () => {
    if (!S.addFolder.trim()) { S.errors = { addFolder: 'Enter a folder, or pick one with Browse…' }; return render(); }
    S.errors = {};
    try { await api('POST', '/api/projects/add', { folder: S.addFolder.trim() }); S.addFolder = ''; loadProjects(); }
    catch (err) { S.errors = { addFolder: err.message[0].toUpperCase() + err.message.slice(1) }; render(); }
  },
  'mp-lookup': lookupMarket,
  'mp-add': () => { S.custom = { repo: S.mp.repo, branch: S.mpBranch }; addCustom(); },
  'start-over': startOver,
  quit: async () => {
    if (!confirm('Quit Bench Wizard? Benches you started keep running.')) return;
    try { await api('POST', '/api/quit', {}); } catch (err) { return window.alert(err.message); }
    document.body.innerHTML = '<main class="main"><p class="lead">Bench Wizard has stopped. You can close this tab; open Bench Wizard again to come back.</p></main>';
  },
  'new-setup': () => { detachRun(); go(S.wizStep || 'project'); S.wizStep = ''; },
  'dismiss-run': async (arg) => {
    try { await api('POST', '/api/run/dismiss', { run: arg }); } catch (err) { return window.alert(err.message); }
    if (S.run && S.run.run_id === arg) ACTIONS['new-setup']();
    refreshRuns();
  },
  'reveal-extension': async () => {
    try {
      const res = await api('POST', '/api/extension/reveal', {});
      if (!res.ok) window.alert('Could not open a file manager here. The folder is:\n' + res.folder);
    } catch (err) { window.alert(err.message); }
  },
  'toggle-pw': () => { S.showPw = !S.showPw; render(); },
  copy: (arg, el) => {
    const text = arg === 'log' ? (S.logs[S.activeStep] || []).join('\n') : arg;
    navigator.clipboard.writeText(text).then(() => { const old = el.textContent; el.textContent = 'Copied'; setTimeout(() => { el.textContent = old; }, 1200); });
  },
};

Object.assign(ACTIONS, BENCH_ACTIONS);

document.addEventListener('click', (ev) => {
  const el = ev.target.closest('[data-act]');
  if (!el || el.disabled) return;
  ev.preventDefault();
  ACTIONS[el.dataset.act](el.dataset.arg, el);
});

document.addEventListener('input', (ev) => {
  const el = ev.target;
  if (el.dataset.bind) {
    setPath(el.dataset.bind, el.value);
    if (el.dataset.bind === 'folder') S.folderEdited = true;
    if (el.dataset.bind === 'site') S.siteEdited = true;
    if (el.dataset.bind === 'rootPw' && S.rootOk !== null && S.step === 'check') {
      // Patch in place: re-rendering here would swallow a click on Verify.
      S.rootOk = null;
      $('root-note').innerHTML = '';
      document.querySelector('[data-act=next]').disabled = true;
    }
    if (el.dataset.bind === 'bp.log.q') paintBenchLog();
    const live = $('live');
    if (live) live.innerHTML = liveSummary();
  } else if (el.dataset.port) {
    const bench = S.proj.benches.find((b) => b.folder === el.dataset.port);
    const value = /^\d+$/.test(el.value.trim()) ? Number(el.value.trim()) : el.value.trim();
    const edits = Object.assign({}, S.portEdits[el.dataset.port], { [el.dataset.key]: value });
    for (const key of Object.keys(edits)) if (edits[key] === bench.ports[key]) delete edits[key];
    if (Object.keys(edits).length) S.portEdits[el.dataset.port] = edits; else delete S.portEdits[el.dataset.port];
    el.classList.toggle('changed', value !== bench.ports[el.dataset.key]);
    const apply = $('ports-apply');  // patched in place: a re-render would take the focus away
    const n = Object.keys(S.portEdits).length;
    apply.disabled = !n; apply.textContent = 'Apply port changes' + (n ? ` (${n})` : '');
  } else if (el.dataset.branch) {
    S.apps[el.dataset.branch].branch = el.value.trim();
  }
});

document.addEventListener('change', (ev) => {
  const el = ev.target;
  if (el.dataset.app) toggleApp(el.dataset.app, el.checked);
  else if (el.dataset.projsite) S.projSite[el.dataset.projsite] = el.value;
  else if (el.dataset.msite) { S.migrateSites[el.dataset.msite] = el.checked; render(); }
  else if (el.dataset.bplog !== undefined) openLog(el.value);
  else if (el.dataset.check) {
    setPath(el.dataset.check, el.checked);
    if (el.dataset.check === 'follow' && el.checked) paintLog();
    if (el.dataset.check.startsWith('bp.log')) paintBenchLog();
  }
  else if (el.dataset.bind === 'restore.sql') inspectBackup();
});

document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' || !ev.target.dataset || !ev.target.dataset.bind) return;
  if (ev.target.dataset.bind === 'rootPw') verifyRoot();
  if (ev.target.dataset.bind === 'benchFolder') loadBench();
  if (ev.target.dataset.bind.startsWith('custom.')) addCustom();
  if (ev.target.dataset.bind === 'mpQuery') lookupMarket();
  if (ev.target.dataset.bind === 'addFolder') ACTIONS['proj-add']();
  if (ev.target.dataset.bind === 'bp.fcDomain' || ev.target.dataset.bind === 'bp.fcUrl') ACTIONS['bp-fc-connect']();
});

async function boot() {
  if (!TOKEN) {
    $('view').innerHTML = '<div class="note bad">' + INFO + '<span>This page needs the full address printed in the terminal where Bench Wizard was started, including <span class="mono">?token=…</span>.</span></div>';
    return;
  }
  try {
    S.catalog = await api('GET', '/api/catalog');
  } catch (err) {
    $('view').innerHTML = '<div class="note bad">' + INFO + '<span>' + esc(err.message) + '. Use the address printed in the terminal.</span></div>';
    return;
  }
  S.version = S.catalog.default_version;
  applyPreset(S.catalog.default_preset);
  await refreshRuns();
  const running = S.runs.find((r) => r.state === 'running');
  if (running) return attachRun(running.run_id);
  render();
}

// ---------------------------------------------------------------- runs list (rail)
const RUN_ICON = { done: icon('check', 18), failed: icon('priority_high', 18), cancelled: icon('priority_high', 18), running: '<span class="spin"></span>' };

function runLabel(s) {
  return s.title ? s.title.toLowerCase() : s.site;
}

// Painted on its own, so polling never re-renders (and so never disturbs) the form being filled in.
function paintRuns() {
  const el = $('runs');
  const away = S.run || S.step === 'projects' || S.step === 'bench';
  el.innerHTML = `<div class="rrow ${S.step === 'projects' || S.step === 'bench' ? 'current' : ''}"><button class="rbtn" data-act="projects">
      <span class="ic waiting">${icon('apps', 18)}</span><span class="t"><span>Projects</span><small>All benches, sites and ports</small></span></button></div>`
    + `<div class="rhead"><span>${S.runs.length ? 'Runs' : ''}</span>${away ? `<button class="btn sm" data-act="new-setup">${icon('add', 18)}New setup</button>` : ''}</div>`
    + S.runs.map((r) => `<div class="rrow ${S.run && S.run.run_id === r.run_id ? 'current' : ''}">
      <button class="rbtn" data-act="open-run" data-arg="${esc(r.run_id)}" title="${esc(r.summary.folder)}">
        <span class="ic ${r.state === 'cancelled' ? 'failed' : esc(r.state)}">${RUN_ICON[r.state] || '<i class="pip"></i>'}</span>
        <span class="t"><span class="mono">${esc(runLabel(r.summary))}</span><small>${esc(tilde(r.summary.folder))}</small></span></button>
      ${r.state === 'running' ? '' : `<button class="rx" data-act="dismiss-run" data-arg="${esc(r.run_id)}" aria-label="Remove ${esc(runLabel(r.summary))} from the list">${icon('close')}</button>`}</div>`).join('');
}

async function refreshRuns() {
  let res;
  try { res = await api('GET', '/api/runs'); } catch (err) { return; }
  const before = JSON.stringify(S.runs.map((r) => [r.run_id, r.state]));
  S.runs = res.runs;
  if (JSON.stringify(S.runs.map((r) => [r.run_id, r.state])) !== before) paintRuns();
}

// ---------------------------------------------------------------- appearance
// shortcut: the choice is kept in this browser only (localStorage); keep it in state_root if it must follow the user.
const PALETTES = [['ocean', 'Ocean', 252, 1], ['teal', 'Teal', 195, 1], ['forest', 'Forest', 148, 1], ['violet', 'Violet', 295, 1],
  ['rose', 'Rose', 12, 1], ['amber', 'Amber', 62, 0.85], ['slate', 'Slate', 250, 0.25]];
const MODES = [['light', 'Light', 'light_mode'], ['dark', 'Dark', 'dark_mode'], ['system', 'System', 'contrast']];
const SCENES = [['none', 'None'], ['mountains', 'Mountains'], ['waves', 'Waves'], ['dots', 'Dots'], ['image', 'Photo']];
const BG_MAX = 2400;  // px on the long side: sharp on a large screen, small enough for browser storage
const SCENE_SVG = {
  mountains: '<svg viewBox="0 0 1440 400" preserveAspectRatio="xMidYMax slice"><circle class="sun" cx="1130" cy="120" r="46"/>'
    + '<path class="l1" d="M0 250L150 160L260 220L410 100L540 200L660 135L790 215L930 85L1070 190L1200 125L1320 195L1440 140V400H0Z"/>'
    + '<path class="l2" d="M0 300L130 235L270 295L400 215L560 305L700 225L840 290L990 200L1130 280L1270 225L1440 285V400H0Z"/>'
    + '<path class="l3" d="M0 352C180 300 360 336 540 330S900 300 1080 336S1320 330 1440 320V400H0Z"/></svg>',
  waves: '<svg viewBox="0 0 1440 400" preserveAspectRatio="xMidYMax slice">'
    + '<path class="l1" d="M0 210C240 150 480 270 720 210S1200 150 1440 210V400H0Z"/>'
    + '<path class="l2" d="M0 275C260 235 460 325 720 280S1180 235 1440 285V400H0Z"/>'
    + '<path class="l3" d="M0 340C300 305 520 370 760 338S1200 318 1440 345V400H0Z"/></svg>',
};
const darkQuery = matchMedia('(prefers-color-scheme: dark)');
const theme = { palette: 'ocean', mode: 'system', scene: 'none', dim: 72 };
let bgImage = '';  // the user's own background, as a JPEG data URL
try { Object.assign(theme, JSON.parse(localStorage.getItem('bw-theme') || '{}')); bgImage = localStorage.getItem('bw-bg') || ''; } catch (e) { /* private window: defaults */ }

function applyTheme(animate) {
  const set = () => {
    const d = document.documentElement;
    const scene = theme.scene === 'image' && !bgImage ? 'none' : theme.scene;
    d.dataset.palette = theme.palette; d.dataset.scene = scene;
    d.dataset.mode = theme.mode === 'system' ? (darkQuery.matches ? 'dark' : 'light') : theme.mode;
    d.style.setProperty('--dim', theme.dim + '%');
    $('scene').innerHTML = SCENE_SVG[scene] || '';
    $('scene').style.backgroundImage = scene === 'image' ? `url("${bgImage}")` : '';
    $('tp-photo').style.backgroundImage = bgImage ? `url("${bgImage}")` : '';
    $('tp-photo').innerHTML = bgImage ? '' : icon('add_photo_alternate', 22);
    $('tp-image').hidden = scene !== 'image';
    const now = { palette: theme.palette, mode: theme.mode, scene };
    document.querySelectorAll('#theme [data-act=theme]').forEach((b) => {
      const [key, value] = b.dataset.arg.split(':');
      b.setAttribute(b.getAttribute('role') === 'radio' ? 'aria-checked' : 'aria-pressed', String(now[key] === value));
    });
  };
  // shortcut: the crossfade needs the View Transitions API (Chromium, Safari 18); elsewhere the change is instant.
  if (animate && document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(set);
  else set();
}

$('theme-btn').innerHTML = icon('palette', 22);
$('theme').innerHTML = `<div class="tp-h"><h2>Appearance</h2>
    <button class="iconbtn" popovertarget="theme" popovertargetaction="hide" aria-label="Close">${icon('close')}</button></div>
  <div><div class="tp-l" id="tp-colour">Colour</div><div class="swatches" role="radiogroup" aria-labelledby="tp-colour">${PALETTES.map(([id, label, h, c]) => `<button class="sw" role="radio" aria-label="${label}" title="${label}" style="--h:${h};--c:${c}" data-act="theme" data-arg="palette:${id}">${icon('check', 18)}</button>`).join('')}</div></div>
  <div><div class="tp-l">Mode</div><div class="seg">${MODES.map(([id, label, ic]) => `<button data-act="theme" data-arg="mode:${id}">${icon(ic, 18)}${label}</button>`).join('')}</div></div>
  <div><div class="tp-l">Background</div><div class="scenes">${SCENES.map(([id, label]) => `<button data-act="theme" data-arg="scene:${id}"><span class="thumb ${id}" ${id === 'image' ? 'id="tp-photo"' : ''}>${SCENE_SVG[id] || ''}</span>${label}</button>`).join('')}</div>
    <div class="tp-image" id="tp-image" hidden>
      <label class="tp-dim">Dim<input type="range" id="tp-dim" min="45" max="90" value="${theme.dim}" aria-label="Dim the photo so text stays readable"></label>
      <button class="btn sm" data-act="bg-pick">Change image</button><button class="btn sm danger" data-act="bg-remove">Remove</button>
    </div>
    <p class="err" id="tp-msg" role="status"></p>
    <input type="file" id="tp-file" accept="image/*" hidden></div>`;
const saveTheme = () => { try { localStorage.setItem('bw-theme', JSON.stringify(theme)); } catch (e) { /* not kept: still applied now */ } };
ACTIONS.theme = (arg) => {
  if (arg === 'scene:image' && !bgImage) return $('tp-file').click();
  const [key, value] = arg.split(':');
  theme[key] = value;
  saveTheme();
  applyTheme(true);
};
ACTIONS['bg-pick'] = () => $('tp-file').click();
ACTIONS['bg-remove'] = () => {
  bgImage = '';
  try { localStorage.removeItem('bw-bg'); } catch (e) { /* nothing kept */ }
  ACTIONS.theme('scene:none');
};

// The user's own background: scaled down and re-encoded here, so a 10 MB phone photo is kept as a few hundred KB.
async function setBackground(file) {
  const msg = $('tp-msg');
  msg.textContent = '';
  let url;
  try {
    const img = await createImageBitmap(file);
    const scale = Math.min(1, BG_MAX / Math.max(img.width, img.height));
    const canvas = Object.assign(document.createElement('canvas'), { width: Math.round(img.width * scale), height: Math.round(img.height * scale) });
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = getComputedStyle(document.body).backgroundColor;  // under a transparent PNG
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    url = canvas.toDataURL('image/jpeg', 0.82);
  } catch (err) {
    msg.textContent = `${file.name} cannot be read as an image here. Pick a JPEG, PNG or WebP file.`;
    return;
  }
  bgImage = url;
  try { localStorage.setItem('bw-bg', url); } catch (err) {
    msg.textContent = 'Shown for now, but this browser has no room to keep it after you close the window.';
  }
  ACTIONS.theme('scene:image');
}
$('tp-file').addEventListener('change', (ev) => { if (ev.target.files[0]) setBackground(ev.target.files[0]); ev.target.value = ''; });
$('tp-dim').addEventListener('input', (ev) => { theme.dim = Number(ev.target.value); saveTheme(); applyTheme(false); });
darkQuery.addEventListener('change', () => { if (theme.mode === 'system') applyTheme(true); });
// Safari before 17 has no popover: there the palette button and Close toggle the panel by hand.
if (!Object.prototype.hasOwnProperty.call(HTMLElement.prototype, 'popover')) {
  document.addEventListener('click', (ev) => {
    if (ev.target.closest('[popovertarget=theme]')) $('theme').classList.toggle('open');
    else if (!ev.target.closest('#theme')) $('theme').classList.remove('open');
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') $('theme').classList.remove('open'); });
}
applyTheme(false);

boot();
setInterval(pollExtension, 2000);
setInterval(() => { if (!document.hidden && S.catalog) refreshRuns(); }, 2000);
