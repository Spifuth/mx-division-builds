#!/usr/bin/env node
/**
 * Checks that the data sources configured in .env.local are actually fetchable
 * *by a browser* from the app's own origin.
 *
 * This exists because a plain `curl` check is not enough. The upstream provider
 * (buildstation.app) returns 200 to anyone, but only sends an
 * Access-Control-Allow-Origin header for https://mxswat.github.io. curl ignores
 * CORS, so the data looks reachable from the server while every browser request
 * is blocked -- and dataImporter's rejection surfaces as App.vue's misleading
 * "too many people are connected to the server" screen.
 *
 * Usage:  node scripts/check-data-sources.mjs [appOrigin]
 *         appOrigin defaults to http://$DEV_HOST:$DEV_PORT
 */
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

const env = Object.fromEntries(
  readFileSync(join(root, ".env.local"), "utf8")
    .split("\n")
    .filter((l) => l.trim() && !l.trim().startsWith("#"))
    .map((l) => {
      const i = l.indexOf("=");
      return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
    })
);

const origin =
  process.argv[2] || `http://${env.DEV_HOST || "localhost"}:${env.DEV_PORT || 8080}`;

const dataUrls = Object.entries(env)
  .filter(([k]) => k.startsWith("VUE_APP_DATA_URL_"))
  .sort();

if (!dataUrls.length) {
  console.error("No VUE_APP_DATA_URL_* entries found in .env.local");
  process.exit(1);
}

console.log(`app origin: ${origin}`);
console.log(`checking ${dataUrls.length} data sources as a browser would\n`);

let failed = 0;

for (const [key, raw] of dataUrls) {
  const url = new URL(raw, origin);
  const crossOrigin = url.origin !== origin;
  const name = key.replace("VUE_APP_DATA_URL_", "");

  let verdict, detail;
  try {
    const res = await fetch(url, { headers: { Origin: origin } });
    const body = await res.text();
    const acao = res.headers.get("access-control-allow-origin");

    if (!res.ok) {
      verdict = "FAIL";
      detail = `HTTP ${res.status}`;
    } else if (crossOrigin && acao !== origin && acao !== "*") {
      // This is the browser-only failure a curl check cannot see.
      verdict = "FAIL";
      detail = `cross-origin and CORS-blocked (allow-origin: ${acao ?? "absent"})`;
    } else if (!body.includes(",") || body.trim().split("\n").length < 2) {
      verdict = "FAIL";
      detail = `200 but not usable CSV (${body.length} bytes)`;
    } else {
      verdict = "OK";
      const rows = body.trim().split("\n").length - 1;
      detail = `${crossOrigin ? "cross-origin, CORS ok" : "same-origin"}, ${rows} rows`;
    }
  } catch (e) {
    verdict = "FAIL";
    detail = e.message;
  }

  if (verdict === "FAIL") failed++;
  console.log(`${verdict.padEnd(5)} ${name.padEnd(22)} ${detail}`);
}

console.log(
  failed
    ? `\n${failed}/${dataUrls.length} data sources are NOT browser-fetchable.`
    : `\nAll ${dataUrls.length} data sources are browser-fetchable from ${origin}.`
);
process.exit(failed ? 1 : 0);
