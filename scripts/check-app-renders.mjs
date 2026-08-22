/**
 * Does the app actually RENDER in a browser?
 *
 * Every other check in this repo runs from the server's vantage point.
 * `npm run check` proves the data is reachable and well-shaped; `npm test`
 * proves the pure math; `npm run build-prod` proves it compiles. All three
 * were green while the app rendered nothing at all, because a Vite dev server
 * returns transformed module *text* without executing it -- a 200 on every
 * module says the graph resolves, not that it runs.
 *
 * This drives a real headless Chromium over the DevTools protocol, loads the
 * app, and fails if the browser threw or if #app is still empty. It is the
 * only check here that stands where the user stands.
 *
 *   npm run check:render http://<host>:8090/
 *
 * Needs the browser sidecar: ./scripts/dev.sh --profile tools up -d browser
 */
const appUrl = process.argv[2];
const cdpBase = process.env.CDP_URL || "http://browser:9222";
const SETTLE_MS = Number(process.env.RENDER_SETTLE_MS || 12000);

if (!appUrl) {
  console.error("usage: npm run check:render <app-url>");
  console.error("  e.g. npm run check:render http://<TAILNET_IP>:8090/");
  console.error("  No default on purpose: the URL decides which host header");
  console.error("  Vite sees, and that is itself a thing this check catches.");
  process.exit(2);
}

const fail = (msg) => { console.log(`FAIL  ${msg}`); process.exitCode = 1; };

// Chrome's DevTools endpoint rejects a Host header that is not localhost or a
// bare IP -- the same class of DNS-rebinding guard Vite grew in 5.4. Reaching
// it as `browser:9222` returns 500, so resolve the name and speak to the IP.
const { lookup } = await import("node:dns/promises");
const cdpUrl = new URL(cdpBase);
if (!/^\d+\.\d+\.\d+\.\d+$/.test(cdpUrl.hostname) && cdpUrl.hostname !== "localhost") {
  try { cdpUrl.hostname = (await lookup(cdpUrl.hostname)).address; } catch {}
}
const cdp = cdpUrl.origin;

let version;
try {
  version = await (await fetch(`${cdp}/json/version`)).json();
} catch {
  console.error(`could not reach a browser at ${cdp} (from ${cdpBase})`);
  console.error("start it with: ./scripts/dev.sh --profile tools up -d browser");
  process.exit(2);
}
console.log(`browser: ${version.Browser}`);
console.log(`target : ${appUrl}\n`);

const tab = await (await fetch(
  `${cdp}/json/new?${encodeURIComponent(appUrl)}`, { method: "PUT" }
)).json();

const ws = new WebSocket(tab.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const call = (method, params = {}) =>
  new Promise((resolve) => { pending.set(++id, resolve); ws.send(JSON.stringify({ id, method, params })); });

const errors = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); return; }
  if (m.method === "Runtime.exceptionThrown") {
    const d = m.params.exceptionDetails;
    errors.push(`${d.exception?.description || d.text} (${d.url || "?"}:${d.lineNumber})`);
  }
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") {
    errors.push(`${m.params.entry.text}${m.params.entry.url ? ` (${m.params.entry.url})` : ""}`);
  }
};
await new Promise((r) => { ws.onopen = r; });
await call("Runtime.enable");
await call("Log.enable");
await new Promise((r) => setTimeout(r, SETTLE_MS));

const probe = await call("Runtime.evaluate", {
  expression: `(() => {
    const app = document.querySelector('#app');
    return JSON.stringify({
      found: !!app,
      children: app ? app.children.length : 0,
      text: app ? app.innerText.slice(0, 200) : '',
      title: document.title,
    });
  })()`,
  returnByValue: true,
});
const dom = JSON.parse(probe?.result?.value || "{}");

for (const e of errors) fail(`browser error: ${e}`);
if (!dom.found) fail("#app is not in the document -- the page itself did not load");
else if (!dom.children) fail("#app is empty -- the page loaded but Vue never mounted");
else console.log(`OK    #app mounted with ${dom.children} child element(s)`);

// App.vue's catch-all screen is a *successful* mount showing a failure, so a
// child count above zero is not on its own proof the app works.
if (/too many people are connected/i.test(dom.text || ""))
  fail("the app mounted but is showing its catch-all data-failure screen");

await fetch(`${cdp}/json/close/${tab.id}`).catch(() => {});
ws.close();

console.log(
  process.exitCode
    ? `\n${errors.length} browser error(s); the app does not render.`
    : `\nThe app renders. This does not prove it is CORRECT -- only that it mounted without throwing.`
);
