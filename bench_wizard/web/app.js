'use strict';

const STEPS = [['project', 'Project'], ['check', 'System check'], ['apps', 'Apps'], ['review', 'Review'],
  ['run', 'Run'], ['done', 'Done']];
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const S = {
  step: 'project', reached: 0, catalog: null, errors: {},
  version: '', preset: '', folder: '', site: '', db: '', pw: '', folderEdited: false, siteEdited: false,
  siteData: 'new',
  restore: { sql: '', public: '', private: '', backup_key: '', site_key: '', mute_emails: true, pause_scheduler: true },
  backup: null, backupMsg: '', backupPath: '',
  checks: null, mac: false, checksVersion: '', checking: false, sudo: '', installing: '', installLogs: {}, installMsg: '',
  toolsInstalled: false, rootPw: '', rootOk: null, rootMsg: '', rootBusy: false,
  apps: {}, appsKey: '', custom: { repo: '', branch: '' }, customMsg: '', customBusy: false, depWarn: null,
  importText: '', importBusy: false, importMsg: '', importErrors: [],
  plan: null, planning: false, planMsg: '',
  run: null, activeStep: '', follow: true, logs: {}, cleanup: true, runMsg: '', showPw: false,
};

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tilde = (p) => (S.catalog && p.startsWith(S.catalog.home + '/') ? '~' + p.slice(S.catalog.home.length) : p);
const profile = () => S.catalog.frappe_versions[S.version];
const ARROW = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
const INFO = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:none"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>';

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
  const backupApps = S.siteData === 'restore' && S.backup ? Object.keys(S.backup.apps) : [];
  const key = [S.version, S.preset, backupApps.join(',')].join('|');
  if (key === S.appsKey) return;
  S.appsKey = key;
  S.depWarn = null;
  const next = {};
  const preset = S.catalog.presets[S.preset];
  for (const [name, app] of Object.entries(S.catalog.apps)) {
    const branch = app.branches[S.version] || '';
    const inBackup = backupApps.includes(name) && name !== 'frappe';
    next[name] = {
      title: app.title, description: app.description, repo: app.repo, requires: app.requires,
      private: app.private, local: !!app.local, custom: false, branch, available: !!branch,
      locked: name === 'frappe', inBackup, fromPreset: preset.apps.includes(name),
      selected: !!branch && (name === 'frappe' || preset.apps.includes(name) || inBackup),
    };
  }
  for (const [name, app] of Object.entries(S.apps)) if (app.custom) next[name] = app;
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

const selectedApps = () => Object.entries(S.apps).filter(([, a]) => a.selected);

// ---------------------------------------------------------------- rendering
function render() {
  const index = STEPS.findIndex(([id]) => id === S.step);
  S.reached = Math.max(S.reached, index);
  const locked = !!S.run;
  $('progress').style.width = Math.round(((index + (S.step === 'done' ? 1 : 0.5)) / STEPS.length) * 100) + '%';
  $('steps').innerHTML = STEPS.map(([id, label], i) => {
    const failed = id === 'run' && S.run && ['failed', 'cancelled'].includes(S.run.state);
    const cls = id === S.step ? 'current' : (i < index ? 'done' : '');
    const mark = cls === 'done' ? '✓' : i + 1;
    const canGo = !locked && i < index && i < 4;  // forward moves go through Continue and its checks
    const inner = `<span class="n">${mark}</span>${esc(label)}`;
    return canGo ? `<button class="vstep link ${cls}" data-act="goto" data-arg="${id}">${inner}</button>`
      : `<span class="vstep ${cls} ${failed ? 'failed' : ''}">${inner}</span>`;
  }).join('');
  $('rail-foot').textContent = locked ? 'Closing this tab does not stop the run. Reopen the URL shown in the terminal to come back.'
    : 'Nothing is written to disk until you press Start setup on the Review screen.';
  const wide = S.step === 'run';
  $('view').className = 'col' + (wide ? ' wide' : '');
  $('footer').className = 'in2' + (wide ? ' wide' : '');
  const [view, footer] = VIEWS[S.step]();
  $('view').innerHTML = view;
  $('footer').innerHTML = footer;
  if (S.step === 'run') paintLog();
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

function viewProject() {
  const c = S.catalog;
  const versions = Object.entries(c.frappe_versions).map(([id, v]) => radio('version', id, id === S.version,
    esc(v.title) + (v.default ? ' <span class="badge blue">Default</span>' : ''),
    `Python ${v.python} · Node ${v.node} · branch ${id}`)).join('');
  const presets = Object.entries(c.presets).map(([id, p]) => {
    const why = presetProblem(p, S.version);
    return radio('preset', id, id === S.preset, esc(p.title) + (p.local ? ' <span class="badge">Local</span>' : ''),
      p.description, why ? `<span class="why">${esc(why)}</span>` : '', !!why);
  }).join('');
  const r = S.restore;
  const backupLine = S.backupMsg ? `<span class="err">${esc(S.backupMsg)}</span>`
    : S.backup && S.backupPath === r.sql ? `<span class="okmsg">${Object.keys(S.backup.apps).length
      ? 'Found in backup: ' + esc(Object.entries(S.backup.apps).map(([n, v]) => n + ' ' + v).join(', '))
      : 'Could not read the app list from this backup; app checks will be skipped'}</span>` : '';
  const restore = S.siteData !== 'restore' ? '' : `
    <div class="grid2">
      <div>${field('Database backup', 'restore.sql', { mono: true, value: r.sql, placeholder: '~/backups/site-database.sql.gz', hint: 'Path to a .sql or .sql.gz file on this machine' })}${backupLine}</div>
      ${field('Public files <span class="hint">(optional)</span>', 'restore.public', { mono: true, value: r.public, placeholder: '~/backups/site-files.tar' })}
      ${field('Private files <span class="hint">(optional)</span>', 'restore.private', { mono: true, value: r.private, placeholder: '~/backups/site-private-files.tar' })}
      ${field('Backup encryption key <span class="hint">(optional)</span>', 'restore.backup_key', { type: 'password', value: r.backup_key, hint: 'Only for backups that were encrypted when taken' })}
      ${field('Source site encryption key <span class="hint">(optional)</span>', 'restore.site_key', { type: 'password', value: r.site_key, hint: 'encryption_key from the source site_config.json. Without it, passwords stored inside the site cannot be decrypted.' })}
    </div>
    <label class="tog"><input type="checkbox" data-check="restore.mute_emails" ${r.mute_emails ? 'checked' : ''}><span><strong>Mute outgoing email</strong><br><span class="hint">So the restored copy cannot email real customers</span></span></label>
    <label class="tog"><input type="checkbox" data-check="restore.pause_scheduler" ${r.pause_scheduler ? 'checked' : ''}><span><strong>Pause scheduler</strong><br><span class="hint">So background jobs and integrations from the source do not run</span></span></label>
    <p class="lead small">Paths are typed or pasted: a browser cannot tell this tool where a chosen file lives.</p>`;
  const view = `
    <div class="head"><h1>Project</h1><p class="lead">Pick the Frappe version and a preset. Folder and site name follow the preset until you edit them.</p></div>
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
      <td><span class="st ${status}">${status === 'installing' ? '<span class="spin"></span>' : '●'} ${STATUS[status]}</span></td></tr>`;
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
  const footer = `<button class="btn" data-act="back">← Back</button>
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
      a.fromPreset && a.selected && !a.locked ? '<span class="badge">from preset</span>' : '',
      !a.available ? `<span class="badge amber">Not available for ${esc(profile().title)}</span>` : '',
    ].join(' ');
    const needs = (a.requires || []).filter((r) => r !== 'frappe');
    return `<div class="app ${a.selected ? 'sel' : ''} ${a.available ? '' : 'dis'}">
      <input type="checkbox" data-app="${esc(name)}" ${a.selected ? 'checked' : ''} ${a.locked || !a.available ? 'disabled' : ''} aria-label="${esc(a.title)}">
      <div class="body"><div class="t">${esc(a.title)} ${badges}</div><div class="d">${esc(a.description)}</div>
        <div class="repo mono">${esc(a.repo)}</div>
        <div class="br"><span style="color:var(--muted)">Branch</span>
          <input class="sm" data-branch="${esc(name)}" value="${esc(a.branch)}" ${a.locked || !a.available ? 'disabled' : ''} aria-label="Branch for ${esc(a.title)}">
          ${needs.length ? `<span class="badge">requires ${esc(needs.join(', '))}</span>` : ''}
          ${a.custom ? `<button class="btn sm" data-act="remove-custom" data-arg="${esc(name)}">Remove</button>` : ''}</div>
      </div></div>`;
  }).join('');
  const add = `<div class="app add"><div class="t">+ Add a custom app</div>
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
  const view = `<div class="head"><h1>Apps</h1><p class="lead">Preset <strong>${esc(S.catalog.presets[S.preset].title)}</strong> on Frappe <strong>${esc(profile().title)}</strong>.
    Selecting an app also selects what it requires. The install order is worked out from each app's own requirements on the next screen.</p></div>
    ${warn}<div class="grid2">${cards}${add}</div>${importCard}`;
  const footer = `<button class="btn" data-act="back">← Back</button><div class="right">
    <span class="lead small">${names.length} app${names.length === 1 ? '' : 's'} selected: ${esc(names.join(', '))}</span>
    <button class="btn primary" data-act="next">Continue to review ${ARROW}</button></div>`;
  return [view, footer];
}

function viewReview() {
  const back = '<button class="btn" data-act="back">← Back</button>';
  if (S.planning) return ['<div class="head"><h1>Review</h1><p class="lead"><span class="spin"></span> Reading each app\'s requirements and running pre-flight checks…</p></div>', back];
  if (!S.plan) return [`<div class="head"><h1>Review</h1></div><div class="note bad">${INFO}<span>${esc(S.planMsg || 'Could not build the plan.')}</span></div>`,
    back + '<div class="right"><button class="btn" data-act="replan">Try again</button></div>'];
  const p = S.plan;
  const order = ['frappe (' + p.version + ')'].concat(p.apps.map((a) => `${a.name} (${a.branch})`)).join(' → ');
  const added = p.apps.filter((a) => a.added_for).map((a) => `<span class="badge blue">${esc(a.title)} added because ${esc(a.added_for)} needs it</span>`).join(' ');
  const passed = p.checks.filter((c) => c.ok).length;
  const view = `<div class="head"><h1>Review</h1><p class="lead">This is exactly what will run. Go back to change anything; nothing is written until you start.</p></div>
    <section class="card"><div class="row"><h2>Summary</h2><a href="#" data-act="goto" data-arg="project" class="small">Edit project</a></div><div class="kv">
      <div><div class="k">Frappe version</div><div class="v">${esc(p.version_title)} · Python ${esc(p.python)} · Node ${esc(p.node)}</div></div>
      <div><div class="k">Preset</div><div class="v">${esc(S.catalog.presets[S.preset].title)}</div></div>
      <div><div class="k">Bench folder</div><div class="v mono">${esc(tilde(p.folder))}</div></div>
      <div><div class="k">Site</div><div class="v mono">${esc(p.site)}</div></div>
      <div><div class="k">Database</div><div class="v mono">${S.db ? esc(S.db) : '<span style="color:var(--muted)">chosen by bench</span>'}</div></div>
      <div><div class="k">Site data</div><div class="v">${p.restore ? 'Restored from <span class="mono">' + esc(S.restore.sql) + '</span>' : 'New empty site'}</div></div>
      <div class="full"><div class="k">Apps, in install order</div><div class="v mono">${esc(order)}</div>${added ? `<div class="v">${added}</div>` : ''}</div>
    </div></section>
    ${p.notices.length ? `<div class="note info">${INFO}<span>${p.notices.map(esc).join('<br>')}</span></div>` : ''}
    <section class="card"><div class="row"><h2>Pre-flight checks</h2><span class="badge ${p.ok ? 'green' : 'red'}">${passed} of ${p.checks.length} passed</span></div>
      <div class="chk">${p.checks.map((c) => `<div class="crow"><span class="dotc ${c.ok ? 'ok' : 'fail'}">${c.ok ? '✓' : '!'}</span><span>${esc(c.label)}</span></div>`).join('')}</div></section>
    <section class="card"><div class="row"><h2>Commands</h2><span class="lead small">Secrets shown as ••••</span></div>
      <div class="cmd">${p.steps.map((s, i) => `<span class="n">${i + 1}</span>${esc(s.command)}`).join('\n')}</div>
      <p class="lead small">The bench's own Redis is started for the site steps and stopped when the run ends. Every step after the first runs inside the bench folder with the nvm Node ${esc(p.node)} <span class="mono">bin</span> and <span class="mono">~/.local/bin</span> on PATH.</p></section>`;
  const footer = back + `<div class="right"><button class="btn" data-act="replan">Re-run checks</button>
    <span class="lead small">${p.ok ? '' : 'Disabled while any pre-flight check fails'}${esc(S.runMsg)}</span>
    <button class="btn go" data-act="start" ${p.ok ? '' : 'disabled'}>▶ Start setup</button></div>`;
  return [view, footer];
}

const ICON = { done: '✓', failed: '!', running: '<span class="spin"></span>' };

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
  if (r.state === 'failed' && failedStep) {
    const n = steps.indexOf(failedStep) + 1;
    const cleanup = !failedStep.cleanup ? '' : failedStep.cleanup_required
      ? `Retrying first runs <span class="mono">${esc(failedStep.cleanup)}</span>, because this step cannot run over a partial result.`
      : `A partial result may be on disk. Cleanup runs <span class="mono">${esc(failedStep.cleanup)}</span> before retrying.`;
    fail = `<div class="fail">${INFO}<div class="msg"><strong>${esc(failedStep.title)} failed.</strong> ${cleanup}</div>
      ${failedStep.cleanup && !failedStep.cleanup_required ? `<label class="tog"><input type="checkbox" data-check="cleanup" ${S.cleanup ? 'checked' : ''}>Run cleanup first</label>` : ''}
      <button class="btn primary" data-act="retry">Retry step ${n}</button><button class="btn danger" data-act="abandon">Stop here</button></div>`;
  } else if (r.state === 'cancelled') {
    fail = `<div class="fail">${INFO}<div class="msg"><strong>The run was cancelled.</strong> What was created so far is still on disk: the bench folder <span class="mono">${esc(tilde(r.summary.folder))}</span>${steps.some((s) => s.id === 'new-site' && s.state !== 'waiting') ? ' and possibly the site ' + esc(r.summary.site) : ''}. Remove them before setting up the same project again.</div>
      <button class="btn" data-act="restart">Set up another bench</button></div>`;
  }
  const view = `<div class="row" style="align-items:flex-end"><div class="head"><h1>Setting up ${esc(r.summary.site)}</h1>
      <p class="lead">Run <span class="mono">${esc(r.run_id)}</span> · ${doneCount} of ${steps.length} steps done · ${duration(elapsed)} elapsed</p></div>
      <span class="badge ${pill[0]}">● ${esc(pill[1])}</span></div>
    <div class="split"><div class="card flush list"><div class="loghead"><h2>Steps</h2></div>${list}</div>
      <div class="card flush logp"><div class="loghead"><span class="mono c">${esc(active.command)}</span>
        <label class="tog small"><input type="checkbox" data-check="follow" ${S.follow ? 'checked' : ''}>Follow output</label>
        <button class="btn sm" data-act="copy" data-arg="log">Copy log</button></div><pre class="log" id="log"></pre></div></div>${fail}`;
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
  const restore = !s.restore || !(s.restore.mute_emails || s.restore.pause_scheduler) ? '' : `<section class="card"><h2>Restored copy: safety switches</h2>
    <div class="note">${INFO}<span>This site was restored from a backup. ${s.restore.mute_emails ? '<strong>Outgoing email is muted.</strong> ' : ''}${s.restore.pause_scheduler ? '<strong>The scheduler is paused.</strong>' : ''} Turn them back on only if this copy should really send email and run jobs.</span></div>
    ${s.restore.mute_emails ? cmdline(`cd ${folder} && bench --site ${s.site} set-config mute_emails 0`) : ''}
    ${s.restore.pause_scheduler ? cmdline(`cd ${folder} && bench --site ${s.site} set-config pause_scheduler 0`) : ''}</section>`;
  const view = `<div class="hero"><div class="okc"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg></div>
      <div><h1>Bench ready</h1><p>${esc(s.site)} was set up in ${duration(total)}. ${r.steps.length} steps ran; nothing is running yet.</p></div></div>
    <section class="card"><h2>Start the bench</h2>
      ${S.toolsInstalled ? `<div class="note">${INFO}<span><strong>Open a new terminal first.</strong> Tools were installed in this session and are only on PATH in a fresh shell.</span></div>` : ''}
      ${cmdline(`cd ${folder} && nvm use && bench start`)}
      <p class="lead small"><span class="mono">nvm use</span> reads the <span class="mono">.nvmrc</span> in the bench folder, so Node ${esc(s.node)} is picked whatever the machine default is.</p></section>
    <section class="card"><h2>Site</h2><div class="kv">
      <div><div class="k">URL</div><div class="v"><a href="http://localhost:8000" target="_blank" rel="noopener" class="mono">http://localhost:8000</a></div></div>
      <div><div class="k">Site name</div><div class="v mono">${esc(s.site)}</div></div>
      <div><div class="k">Login</div><div class="v mono">Administrator</div></div>
      <div><div class="k">Password</div><div class="v mono">${S.pw ? (S.showPw ? esc(S.pw) : '••••') + ` <a href="#" data-act="toggle-pw" class="small" style="font-family:var(--sans)">${S.showPw ? 'Hide' : 'Show'}</a>` : 'the one you entered in step 1'}</div></div>
      <div><div class="k">Bench folder</div><div class="v mono">${esc(folder)}</div></div>
      <div><div class="k">Developer mode</div><div class="v">On</div></div></div></section>
    ${restore}
    <section class="card"><h2>Installed apps</h2><div class="chips"><span class="chip">frappe <span class="b">${esc(s.version)}</span></span>
      ${s.apps.map((a) => `<span class="chip">${esc(a.name)} <span class="b">${esc(a.branch)}</span></span>`).join('')}</div></section>
    <section class="card"><h2>Update later</h2>${cmdline(`cd ${folder} && nvm use && bench update --pull --build --patch`)}
      ${cmdline(`cd ${tilde(S.catalog.wizard_dir)} && git pull`)}
      <p class="lead small">The first updates the bench and its apps; the second updates Bench Wizard itself, including the shared app catalog.</p></section>`;
  const footer = `<span class="lead small">Logs: <span class="mono">${esc(tilde(r.log_dir))}</span></span>
    <div class="right"><button class="btn" data-act="restart">Set up another bench</button></div>`;
  return [view, footer];
}

const VIEWS = { project: viewProject, check: viewCheck, apps: viewApps, review: viewReview, run: viewRun, done: viewDone };

// ---------------------------------------------------------------- actions
function validateProject() {
  const e = {};
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
  if (!path || S.siteData !== 'restore') return;
  S.backupMsg = '';
  try {
    const res = await api('POST', '/api/backup/inspect', { path });
    if (res.ok) { S.backup = res; S.backupPath = S.restore.sql; } else { S.backup = null; S.backupMsg = res.error; }
  } catch (err) { S.backupMsg = err.message; }
  if (S.step === 'project') render();
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
  const body = {
    version: S.version, folder: S.folder.trim(), site: S.site.trim(), db_name: S.db.trim(), admin_password: S.pw,
    mariadb_root_password: S.rootPw, apps, restore: S.siteData === 'restore' ? S.restore : null,
  };
  try { S.plan = await api('POST', '/api/plan', body); } catch (err) { S.planMsg = err.message; }
  S.planning = false; render();
}

async function startRun() {
  try { await api('POST', '/api/run', {}); } catch (err) { S.runMsg = ' ' + err.message; return render(); }
  S.logs = {}; S.follow = true;
  await attachRun();
}

let source = null;

async function attachRun() {
  if (source) source.close();
  source = new EventSource('/api/run/events?token=' + encodeURIComponent(TOKEN));
  source.onmessage = (msg) => onEvent(JSON.parse(msg.data));
}

async function loadLog(id) {
  try { S.logs[id] = (await api('GET', '/api/run/log?step=' + encodeURIComponent(id))).log.split('\n').filter((l, i, a) => i < a.length - 1 || l); }
  catch (err) { S.logs[id] = S.logs[id] || []; }
  if (S.activeStep === id) paintLog();
}

function onEvent(ev) {
  if (ev.type === 'snapshot') {
    if (ev.state === 'none') return;
    S.run = ev;
    const focus = ev.steps.find((s) => s.state === 'running') || ev.steps.find((s) => s.id === ev.failed)
      || ev.steps.filter((s) => s.state === 'done').pop() || ev.steps[0];
    S.activeStep = focus.id;
    if (S.step !== 'done') S.step = 'run';
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
    step.state = ev.state; step.duration = ev.duration;
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
  S.step = step; S.errors = {}; render(); window.scrollTo(0, 0);
  if (step === 'check') loadChecks(false);
  if (step === 'review') buildPlan();
}

const NEXT = { project: 'check', check: 'apps', apps: 'review' };
const BACK = { check: 'project', apps: 'check', review: 'apps' };

const ACTIONS = {
  goto: (arg) => go(arg),
  back: () => go(BACK[S.step]),
  next: () => {
    if (S.step === 'project' && !validateProject()) return render();
    if (S.step === 'apps' && S.depWarn) return;
    go(NEXT[S.step]);
  },
  version: (arg) => { pickVersion(arg); render(); },
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
  retry: async () => { S.follow = true; try { await api('POST', '/api/run/retry', { cleanup: S.cleanup }); } catch (err) { window.alert(err.message); } },
  cancel: async () => {
    if (!window.confirm('Cancel the run? The current step is stopped. Whatever was created so far stays on disk.')) return;
    try { await api('POST', '/api/run/cancel', {}); } catch (err) { window.alert(err.message); }
  },
  abandon: () => { S.run.state = 'cancelled'; render(); },
  finish: () => { S.step = 'done'; render(); window.scrollTo(0, 0); },
  restart: async () => {
    if (source) source.close();
    try { await api('POST', '/api/run/dismiss', {}); } catch (err) { /* a running run stays */ }
    Object.assign(S, { run: null, plan: null, logs: {}, step: 'project', reached: 0, folderEdited: false, siteEdited: false, appsKey: '', showPw: false });
    applyPreset(S.preset); render();
  },
  'toggle-pw': () => { S.showPw = !S.showPw; render(); },
  copy: (arg, el) => {
    const text = arg === 'log' ? (S.logs[S.activeStep] || []).join('\n') : arg;
    navigator.clipboard.writeText(text).then(() => { const old = el.textContent; el.textContent = 'Copied'; setTimeout(() => { el.textContent = old; }, 1200); });
  },
};

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
    if (el.dataset.bind === 'rootPw' && S.rootOk !== null) {
      // Patch in place: re-rendering here would swallow a click on Verify.
      S.rootOk = null;
      $('root-note').innerHTML = '';
      document.querySelector('[data-act=next]').disabled = true;
    }
    const live = $('live');
    if (live) live.innerHTML = liveSummary();
  } else if (el.dataset.branch) {
    S.apps[el.dataset.branch].branch = el.value.trim();
  }
});

document.addEventListener('change', (ev) => {
  const el = ev.target;
  if (el.dataset.app) toggleApp(el.dataset.app, el.checked);
  else if (el.dataset.check) { setPath(el.dataset.check, el.checked); if (el.dataset.check === 'follow' && el.checked) paintLog(); }
  else if (el.dataset.bind === 'restore.sql') inspectBackup();
});

document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' || !ev.target.dataset || !ev.target.dataset.bind) return;
  if (ev.target.dataset.bind === 'rootPw') verifyRoot();
  if (ev.target.dataset.bind.startsWith('custom.')) addCustom();
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
  const existing = await api('GET', '/api/run');
  if (existing.state !== 'none') {
    if (existing.state === 'done') S.step = 'done';
    return attachRun();
  }
  render();
}

boot();
