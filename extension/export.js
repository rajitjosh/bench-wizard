// Turns Frappe Cloud's installed-app rows into the list Bench Wizard's "Paste an app list" box takes.
// Bench Wizard reads "url" and "branch"; the other keys are for people reading the file.

function toExport(rows) {
  const apps = rows.map((r) => {
    const item = { app: r.app, url: r.repository_url, branch: r.branch, commit: r.hash };
    if (r.tag) item.tag = r.tag;
    if (!r.public) item.private = true;
    return item;
  });
  // frappe first: its branch is the Frappe version to pick in the wizard.
  return apps.filter((a) => a.app === 'frappe').concat(apps.filter((a) => a.app !== 'frappe'));
}

// "/dashboard/sites/<name>/..." -> "<name>", else "".
function siteFromPath(path) {
  const m = /^\/dashboard\/sites\/([^/?#]+)/.exec(path || '');
  return m && m[1] !== 'new' ? decodeURIComponent(m[1]) : '';
}

if (typeof module !== 'undefined') module.exports = { toExport, siteFromPath };
