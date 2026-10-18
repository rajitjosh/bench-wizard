// Calls Frappe Cloud's own dashboard API from inside the open dashboard tab, so the requests carry
// the user's login, CSRF token and current team exactly as the dashboard's do. No key is stored.
// Frappe Cloud moved its dashboard to cloud.frappe.io; frappecloud.com still serves it too.
const DASHBOARDS = ['https://cloud.frappe.io/dashboard', 'https://frappecloud.com/dashboard'];
const $ = (id) => document.getElementById(id);
let tabId = null;
let exported = null;

function show(text, isError) {
  $('msg').textContent = text;
  $('msg').className = isError ? 'error' : 'muted';
  $('msg').hidden = !text;
}

async function call(method, args) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [method, args || {}],
    func: async (method, args) => {
      const headers = { Accept: 'application/json', 'Content-Type': 'application/json; charset=utf-8' };
      const team = localStorage.getItem('current_team') || window.default_team;
      if (team) headers['X-Press-Team'] = team;
      if (window.csrf_token && window.csrf_token !== '{{ csrf_token }}') headers['X-Frappe-CSRF-Token'] = window.csrf_token;
      try {
        const res = await fetch(`/api/method/${method}`, { method: 'POST', headers, body: JSON.stringify(args) });
        const data = await res.json().catch(() => ({}));
        if (res.ok) return { message: data.message };
        if (res.status === 401 || res.status === 403) return { error: 'Not allowed. Are you logged in to Frappe Cloud?' };
        return { error: data._error_message || data.exc_type || `${method} failed (HTTP ${res.status})` };
      } catch (err) {
        return { error: String(err) };
      }
    },
  });
  if (!result) throw new Error('The Frappe Cloud tab did not answer. Reload it and try again.');
  if (result.error) throw new Error(result.error);
  return result.message;
}

async function loadSite(name) {
  $('out').hidden = true;
  show(`Reading apps on ${name}…`);
  try {
    exported = toExport(await call('press.api.site.installed_apps', { name }));
  } catch (err) {
    return show(err.message, true);
  }
  show('');
  const frappe = exported.find((a) => a.app === 'frappe');
  const notes = [];
  if (frappe) notes.push(`Frappe branch: ${frappe.branch}. Pick that version in the wizard.`);
  const privates = exported.filter((a) => a.private).length;
  if (privates) notes.push(`${privates} private repo${privates > 1 ? 's' : ''}: put a token in the URL or add it in the wizard.`);
  notes.push('The site may run an older commit than the branch head; the wizard clones the branch.');
  $('notes').textContent = notes.join(' ');
  $('json').value = JSON.stringify(exported, null, 2);
  $('out').hidden = false;
}

async function main() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  // tab.url is only visible for Frappe Cloud tabs (host permission), so anything else reads as "".
  if (!tab || !DASHBOARDS.some((d) => (tab.url || '').startsWith(d))) {
    return show('Open your Frappe Cloud dashboard (cloud.frappe.io/dashboard), log in, then click this again.', true);
  }
  tabId = tab.id;
  let sites;
  try {
    sites = await call('press.api.site.all');
  } catch (err) {
    return show(err.message, true);
  }
  if (!sites || !sites.length) return show('No sites found for the current team.', true);
  const current = siteFromPath(new URL(tab.url).pathname);
  const select = $('site');
  for (const site of sites) {
    const opt = document.createElement('option');
    opt.value = opt.textContent = site.name;
    select.appendChild(opt);
  }
  select.value = sites.some((s) => s.name === current) ? current : sites[0].name;
  select.addEventListener('change', () => loadSite(select.value));
  $('pick').hidden = false;
  loadSite(select.value);
}

$('send').addEventListener('click', async () => {
  const sent = $('sent');
  $('send').disabled = true;
  const count = await sendToWizard($('site').value, exported).catch(() => 0);
  $('send').disabled = false;
  sent.className = count ? 'muted small' : 'error small';
  sent.textContent = count
    ? `Sent ${exported.length} apps. Switch to Bench Wizard: they are in "Paste an app list" on the Apps step.`
    : 'Bench Wizard is not running on this machine. Start it, or copy the JSON and paste it in.';
});

$('copy').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('json').value);
  $('copy').textContent = 'Copied';
  setTimeout(() => { $('copy').textContent = 'Copy JSON'; }, 1500);
});

$('download').addEventListener('click', () => {
  const blob = new Blob([$('json').value + '\n'], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${$('site').value}-apps.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

main();
