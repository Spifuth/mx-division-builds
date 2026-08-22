#!/usr/bin/env node
/**
 * Checks that every data table the app asks for is actually shipped, served
 * and shaped the way the code expects.
 *
 * The expected table list is extracted from the dataUrl("...") calls in
 * src/utils/dataImporter.js -- the code that does the fetching -- and NOT from
 * a listing of public/data/. Deriving it from the directory would compare the
 * directory against itself: a typo'd dataUrl("brandSetBonuses") would still
 * report every table OK while the running app 404s. The two sides are then
 * reconciled, so both a referenced-but-absent table and a shipped-but-orphaned
 * CSV are failures.
 *
 * Each referenced table is then checked in three layers:
 *
 *   1. REACHABLE -- does the URL answer 200?
 *   2. BROWSER-FETCHABLE -- would a browser be allowed to read the response?
 *      Data is same-origin now that it ships in public/data/, so this normally
 *      passes trivially; the layer stays as a guard for non-same-origin setups
 *      (a CDN or separate data host), where a 200 to curl can still be a hard
 *      block in every browser.
 *   3. USABLE -- do the columns classes.js reads still exist? A 200 with
 *      drifted headers builds an inventory of undefined.
 *
 * Parses with PapaParse deliberately: it is the parser the app itself uses, and
 * several tables quote description fields containing newlines, so naive line
 * splitting overcounts (gearTalents reads as 414 lines but is 206 rows).
 *
 * Usage:  node scripts/check-data-sources.mjs <appOrigin>
 *         e.g. node scripts/check-data-sources.mjs http://10.0.0.5:8090/
 */
import { readdirSync, readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import Papa from "papaparse";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

// Columns each constructor in src/utils/classes.js reads off the raw CSV row.
// Only the universal intersection is required -- gear slots legitimately differ
// (mask alone has "Mod 2", backpack has "Core 2"/"Core 3", holster "Attribute 3").
const GEAR = ["Quality", "Item Name", "Brand", "Core", "Attribute 1", "Attribute 2", "Mod", "Talent"];
const CONTRACTS = {
  mask: GEAR, chest: GEAR, gloves: GEAR, holster: GEAR, kneepads: GEAR, backpack: GEAR,
  weapon: ["Name", "Quality", "RPM", "Base Damage", "Mag Size", "Optimal Range",
           "Reload Speed (ms)", "HSD", "Core 1", "Core 1 Max", "Core 2", "Core 2 Max",
           "Weapon Type", "Variant", "Talent", "Optics", "Under Barrel", "Magazine", "Muzzle"],
  skill: ["Skill ID", "Item Name", "Icon", "Variant", "Quality", "Expertise Bonus",
          "Slot One", "Slot Two", "Slot Three", "Mod 1", "Mod 2", "Mod 3", "Desc"],
};

// --- origin -----------------------------------------------------------------
// Required. There is no useful default: the one-shot tools container cannot
// reach the dev server on "localhost", so a default would only ever produce a
// confusing wall of ECONNREFUSED.
const originArg = process.argv[2];
if (!originArg) {
  console.error("usage: node scripts/check-data-sources.mjs <appOrigin>");
  console.error("");
  console.error("  e.g. npm run check http://10.0.0.5:8090/");
  console.error("  Run `./scripts/dev.sh config` to see the address the app is published on.");
  process.exit(1);
}

let appOrigin;
try {
  appOrigin = new URL(originArg).origin;
} catch {
  console.error(`usage: node scripts/check-data-sources.mjs <appOrigin>`);
  console.error(`  not a valid absolute URL: ${originArg}`);
  process.exit(1);
}

// --- expected tables, from the code that fetches them ------------------------
const importerPath = join(root, "src", "utils", "dataImporter.js");
const importerSrc = readFileSync(importerPath, "utf8");
const referenced = [
  ...new Set(
    [...importerSrc.matchAll(/dataUrl\(\s*["'`]([^"'`]+)["'`]\s*\)/g)].map((m) => m[1])
  ),
].sort();

if (referenced.length === 0) {
  console.error(`no dataUrl("...") calls found in ${importerPath}`);
  console.error("Either the file moved or the call shape changed; this check is blind until fixed.");
  process.exit(1);
}

// --- reconcile against what actually ships -----------------------------------
const dataDir = join(root, "public", "data");
const shipped = new Set(
  readdirSync(dataDir).filter((f) => f.endsWith(".csv")).map((f) => f.replace(/\.csv$/, ""))
);

const missing = referenced.filter((t) => !shipped.has(t));
const orphans = [...shipped].filter((t) => !referenced.includes(t)).sort();

console.log(`app origin: ${appOrigin}`);
console.log(`${referenced.length} tables referenced by src/utils/dataImporter.js`);
console.log(`checking them as a browser would\n`);

let failed = 0;

for (const t of missing) {
  failed++;
  console.log(`${"FAIL".padEnd(5)} ${t.padEnd(22)} referenced by dataImporter.js but public/data/${t}.csv does not exist`);
}
for (const t of orphans) {
  failed++;
  console.log(`${"FAIL".padEnd(5)} ${t.padEnd(22)} orphan: public/data/${t}.csv is shipped but no dataUrl() call references it`);
}

// --- fetch + schema ----------------------------------------------------------
for (const name of referenced) {
  if (missing.includes(name)) continue; // already reported; nothing to fetch

  const url = new URL(`data/${name}.csv`, originArg);
  const crossOrigin = url.origin !== appOrigin;

  let verdict = "OK", detail;
  try {
    const res = await fetch(url, { headers: { Origin: appOrigin } });
    const body = await res.text();
    const acao = res.headers.get("access-control-allow-origin");

    if (!res.ok) {
      verdict = "FAIL";
      detail = `HTTP ${res.status}`;
    } else if (crossOrigin && acao !== appOrigin && acao !== "*") {
      verdict = "FAIL";
      detail = `cross-origin and CORS-blocked (allow-origin: ${acao ?? "absent"})`;
    } else {
      const rows = Papa.parse(body.trim()).data;
      const headers = rows.shift() ?? [];
      const missingCols = (CONTRACTS[name] ?? []).filter((c) => !headers.includes(c));

      if (rows.length < 1) {
        verdict = "FAIL";
        detail = `200 but no data rows (${body.length} bytes)`;
      } else if (missingCols.length) {
        verdict = "FAIL";
        detail = `schema drift -- missing: ${missingCols.join(", ")}`;
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
    ? `\n${failed} problem(s) across ${referenced.length} referenced data sources.`
    : `\nAll ${referenced.length} data sources reachable, CORS-clear and schema-valid from ${appOrigin}.`
);
process.exit(failed ? 1 : 0);
