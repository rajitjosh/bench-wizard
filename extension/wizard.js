// Talks to a running Bench Wizard on this machine. The wizard listens on the first free one of these
// ports (bench_wizard/server.py EXTENSION_PORTS) and accepts these two calls only from an extension.
const WIZARD_PORTS = [47615, 47616, 47617, 47618, 47619];

async function wizardPost(port, path, body) {
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(2000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Tells every running wizard that the extension is installed. Returns their ports.
async function sayHello() {
  const version = chrome.runtime.getManifest().version;
  const found = await Promise.all(WIZARD_PORTS.map((port) =>
    wizardPost(port, '/api/extension/hello', { version })
      .then((data) => (data.app === 'bench-wizard' ? port : null), () => null)));
  return found.filter(Boolean);
}

// Puts the list in the import box of every running wizard. Returns how many got it.
async function sendToWizard(site, apps) {
  const ports = await sayHello();
  const sent = await Promise.all(ports.map((port) =>
    wizardPost(port, '/api/extension/send', { site, apps }).then(() => true, () => false)));
  return sent.filter(Boolean).length;
}
