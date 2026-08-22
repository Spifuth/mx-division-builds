#!/usr/bin/env node
/**
 * Checks the data sources configured in .env.local in three layers, because
 * each one fails differently and only the last two are visible from the server:
 *
 *   1. REACHABLE  -- does the URL answer 200?
 *   2. BROWSER-FETCHABLE -- would a browser be allowed to read the response?
 *      The provider (buildstation.app) returns 200 to anyone but only sends
 *      Access-Control-Allow-Origin for https://mxswat.github.io. curl ignores
 *      CORS, so layer 1 passes while every browser request is blocked, and
 *      dataImporter's rejection surfaces as App.vue's misleading "too many
 *      people are connected to the server" screen.
 *   3. USABLE -- do the columns classes.js reads still exist? A 200 with
 *      drifted headers builds an inventory of undefined.
 *
 * Parses with PapaParse deliberately: it is the parser the app itself uses, and
 * several tables quote description fields containing newlines, so naive line
 * splitting overcounts (gearTalents reads as 414 lines but is 206 rows).
 *
 * Usage:  node scripts/check-data-sources.mjs [appOrigin]
 *         appOrigin defaults to http://$DEV_HOST:$DEV_PORT
 */
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import Papa from "papaparse";

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

// Columns each constructor in src/utils/classes.js reads off the raw CSV row.
// Only the universal intersection is required -- gear slots legitimately differ
// (mask alone has "Mod 2", backpack has "Core 2"/"Core 3", holster "Attribute 3").
const GEAR = ["Quality", "Item Name", "Brand", "Core", "Attribute 1", "Attribute 2", "Mod", "Talent"];
const CONTRACTS = {
  MASK: GEAR, CHEST: GEAR, GLOVES: GEAR, HOLSTER: GEAR, KNEEPADS: GEAR, BACKPACK: GEAR,
  WEAPONS: ["Name", "Quality", "RPM", "Base Damage", "Mag Size", "Optimal Range",
            "Reload Speed (ms)", "HSD", "Core 1", "Core 1 Max", "Core 2", "Core 2 Max",
            "Weapon Type", "Variant", "Talent", "Optics", "Under Barrel", "Magazine", "Muzzle"],
  SKILLS: ["Skill ID", "Item Name", "Icon", "Variant", "Quality", "Expertise Bonus",
           "Slot One", "Slot Two", "Slot Three", "Mod 1", "Mod 2", "Mod 3", "Desc"],
};

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

  let verdict = "OK", detail;
  try {
    const res = await fetch(url, { headers: { Origin: origin } });
    const body = await res.text();
    const acao = res.headers.get("access-control-allow-origin");

    if (!res.ok) {
      verdict = "FAIL";
      detail = `HTTP ${res.status}`;
    } else if (crossOrigin && acao !== origin && acao !== "*") {
      verdict = "FAIL";
      detail = `cross-origin and CORS-blocked (allow-origin: ${acao ?? "absent"})`;
    } else {
      const rows = Papa.parse(body.trim()).data;
      const headers = rows.shift() ?? [];
      const missing = (CONTRACTS[name] ?? []).filter((c) => !headers.includes(c));

      if (rows.length < 1) {
        verdict = "FAIL";
        detail = `200 but no data rows (${body.length} bytes)`;
      } else if (missing.length) {
        verdict = "FAIL";
        detail = `schema drift -- missing: ${missing.join(", ")}`;
      } else {
        const where = crossOrigin ? "cross-origin, CORS ok" : "same-origin";
        const checked = CONTRACTS[name] ? `contract ok` : "no class contract";
        detail = `${where}, ${rows.length} rows, ${headers.length} cols, ${checked}`;
      }
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
    ? `\n${failed}/${dataUrls.length} data sources are NOT usable by the app.`
    : `\nAll ${dataUrls.length} data sources reachable, CORS-clear and schema-valid from ${origin}.`
);
process.exit(failed ? 1 : 0);
