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
 *   2. SAME-ORIGIN BY CONSTRUCTION -- does dataUrl() in dataImporter.js still
 *      build every path relative to getAppRootPath(), with no third-party
 *      host hardcoded in? This used to be a live CORS probe, but after
 *      Phase 2 there is no remote data host left to probe: every URL this
 *      script fetches below is resolved as new URL(relative, originArg),
 *      which is same-origin by definition, so a request-time check of it
 *      can never fail -- it would only ever be testing this script's own
 *      URL math, not the app. What CAN fail is the source itself
 *      regressing: someone hardcoding an absolute URL back into dataUrl(),
 *      reintroducing the third-party dependency Phase 2 removed. This layer
 *      reads dataImporter.js's own source and fails loudly if that happens.
 *   3. USABLE -- do the columns classes.js reads still exist? A 200 with
 *      drifted headers builds an inventory of undefined.
 *   4. ILLUSTRATED -- does every icon filename the shipped data names actually
 *      exist under public/icons/? The CSVs carry filenames, not images, and a
 *      name with no file behind it is silent: the SPA fallback answers the
 *      <img> with 200 text/html and the image simply fails to decode. This
 *      branch shipped 26 such names before the layer existed.
 *
 * Parses with PapaParse deliberately: it is the parser the app itself uses, and
 * several tables quote description fields containing newlines, so naive line
 * splitting overcounts (gearTalents reads as 414 lines but is 206 rows).
 *
 * Usage:  node scripts/check-data-sources.mjs <appOrigin>
 *         e.g. node scripts/check-data-sources.mjs http://<TAILNET_IP>:8090/
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

// Icon filenames live in the data, images live on disk, and only these two
// tables' `Icon` columns are ever read back out and interpolated into an
// <img src>. Scope is deliberately narrow:
//
//   * The gear and weapon CSVs also carry `Icon`/`Icon Ref` columns, but
//     nothing in src/ reads them -- the only two `Icon` reads in the app are
//     classes.js's `skillRaw.Icon` and GearSelectionModal's
//     `BrandsData[name].Icon` -- and the files they name (the-bighorn.png,
//     tardigrade_armor_system.png, ...) are not in this repo at all. They are
//     columns of the upstream snapshot, not app inputs.
//   * The flat public/icons/ root is out of scope: it is named by hardcoded
//     literals in components, not by data, and at least one of its files
//     (handling1.png) is reached through a bare filename in a component data
//     array, so a reconciliation there would produce false failures.
//
// `consumers` is not decoration. If a component stops interpolating
// ./icons/<dir>/ this mapping has gone stale and the layer is testing nothing,
// so a missing marker is a hard failure rather than a silent pass -- the same
// rule layer 2 applies to dataUrl().
const ICON_SETS = [
  {
    table: "brands",
    dir: "brands",
    consumers: ["src/components/Modals/GearSelectionModal.vue"],
  },
  {
    table: "skill",
    dir: "skills",
    consumers: [
      "src/components/SkillSlot.vue",
      "src/components/Modals/SkillsSelectionModal.vue",
    ],
  },
];

// Icons the shipped data names that upstream never drew. Every ref in this
// repository was searched -- master, every origin/* branch and the built
// gh-pages branch -- and these four files exist in none of them; the CSV
// snapshot (buildstation.app, DB.Version 26.0-mdb) is simply ahead of
// upstream's art. They render as broken images today.
//
// This list cannot quietly absorb the next regression: anything missing that
// is NOT named here is still a hard failure, and an entry that IS present on
// disk is also a hard failure, so the list cannot rot into a permanent excuse.
// Delete an entry the moment the file lands.
const KNOWN_GAPS = {
  brands: ["edelweiss_gpz.png", "ortiz_reficere.png"],
  skills: ["smartcover_fortified.png", "smartcover_precision.png"],
};

// --- origin -----------------------------------------------------------------
// Required. There is no useful default: the one-shot tools container cannot
// reach the dev server on "localhost", so a default would only ever produce a
// confusing wall of ECONNREFUSED.
const originArg = process.argv[2];
if (!originArg) {
  console.error("usage: node scripts/check-data-sources.mjs <appOrigin>");
  console.error("");
  console.error("  e.g. npm run check http://<TAILNET_IP>:8090/");
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

// --- layer 2: same-origin by construction ------------------------------------
// There is no remote data host left to probe for CORS, so this is a static
// read of dataUrl()'s own source rather than a request. It fails loudly if
// dataUrl() ever grows an absolute scheme (http://, https://, or a bare
// leading //) -- that is what reintroducing a third-party data host, the
// thing Phase 2 removed, would look like.
const dataUrlDefMatch = importerSrc.match(/const\s+dataUrl\s*=[^\n]*=>\s*`([^`]*)`/);
let sameOriginOk = false, sameOriginDetail;

if (!dataUrlDefMatch) {
  sameOriginDetail = `could not find dataUrl()'s definition in ${importerPath} -- this layer is blind until fixed`;
} else if (/:\/\/|^\s*\/\//.test(dataUrlDefMatch[1])) {
  sameOriginDetail = `dataUrl() now contains an absolute URL (\`${dataUrlDefMatch[1]}\`) -- a remote data host has been reintroduced`;
} else {
  sameOriginOk = true;
  sameOriginDetail = `dataUrl() builds every path relative to getAppRootPath(); no host is hardcoded in`;
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
let warned = 0;

if (!sameOriginOk) failed++;
console.log(`${(sameOriginOk ? "OK" : "FAIL").padEnd(5)} ${"[dataUrl()]".padEnd(22)} ${sameOriginDetail}`);

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

  let verdict = "OK", detail;
  try {
    const res = await fetch(url);
    const body = await res.text();

    if (!res.ok) {
      verdict = "FAIL";
      detail = `HTTP ${res.status}`;
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
        const checked = CONTRACTS[name] ? `contract ok` : "no class contract";
        detail = `${rows.length} rows, ${headers.length} cols, ${checked}`;
      }
    }
  } catch (e) {
    verdict = "FAIL";
    detail = e.message;
  }

  if (verdict === "FAIL") failed++;
  console.log(`${verdict.padEnd(5)} ${name.padEnd(22)} ${detail}`);
}

// --- layer 4: icons the shipped data names -----------------------------------
// Filesystem, not HTTP, on purpose. Over HTTP a missing icon is indistinguish-
// able from a present one without decoding the body, because the SPA fallback
// answers it 200 -- the very failure mode being guarded against. What ships is
// a filesystem fact, so it is checked as one, in both directions, exactly as
// the CSV reconciliation above.
function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]
  );
}
const srcText = walk(join(root, "src"))
  .filter((f) => /\.(vue|js|ts)$/.test(f))
  .map((f) => readFileSync(f, "utf8"))
  .join("\n");

console.log(`\nicons named by the data`);

// Staleness guard: every ./icons/<dir>/ prefix the components actually use has
// to be one this layer knows about. A new icon directory appearing in src/
// without an ICON_SETS entry means the layer is silently not covering it.
const dirsUsedInSrc = new Set(
  [...srcText.matchAll(/\.\/icons\/([A-Za-z0-9_-]+)\//g)].map((m) => m[1])
);
const unmapped = [...dirsUsedInSrc].filter((d) => !ICON_SETS.some((s) => s.dir === d)).sort();
for (const d of unmapped) {
  failed++;
  console.log(`${"FAIL".padEnd(5)} ${d.padEnd(22)} src/ interpolates ./icons/${d}/ but no ICON_SETS entry covers it -- this layer is blind to that directory`);
}

for (const set of ICON_SETS) {
  const label = `[icons/${set.dir}]`;

  // Same rule layer 2 applies to dataUrl(): if the consumer stopped building
  // the path, the mapping is stale and a pass would mean nothing.
  const blind = set.consumers.filter((c) => !readFileSync(join(root, c), "utf8").includes(`./icons/${set.dir}/`));
  if (blind.length) {
    failed++;
    console.log(`${"FAIL".padEnd(5)} ${label.padEnd(22)} ${blind.join(", ")} no longer interpolates ./icons/${set.dir}/ -- mapping is stale and this layer is blind until fixed`);
    continue;
  }

  const csv = readFileSync(join(dataDir, `${set.table}.csv`), "utf8").trim();
  const fromData = new Set(
    Papa.parse(csv, { header: true }).data
      .map((r) => (r.Icon ?? "").trim())
      .filter(Boolean)
  );
  // Some icons in these directories are named by the components directly
  // rather than by the data (skills/skills.png is the empty-slot background).
  // They are legitimate residents, so count them as referenced.
  const fromSrc = new Set(
    [...srcText.matchAll(new RegExp(`\\./icons/${set.dir}/([A-Za-z0-9_.-]+\\.png)`, "g"))].map((m) => m[1])
  );
  const expected = new Set([...fromData, ...fromSrc]);

  const onDisk = new Set(
    readdirSync(join(root, "public", "icons", set.dir), { withFileTypes: true })
      .filter((e) => e.isFile())
      .map((e) => e.name)
  );
  const gaps = new Set(KNOWN_GAPS[set.dir] ?? []);

  const stale = [...gaps].filter((f) => onDisk.has(f)).sort();
  const absent = [...expected].filter((f) => !onDisk.has(f) && !gaps.has(f)).sort();
  const known = [...expected].filter((f) => !onDisk.has(f) && gaps.has(f)).sort();
  const orphans = [...onDisk].filter((f) => !expected.has(f)).sort();

  for (const f of stale) {
    failed++;
    console.log(`${"FAIL".padEnd(5)} ${label.padEnd(22)} ${f} is listed in KNOWN_GAPS but exists on disk -- delete the KNOWN_GAPS entry`);
  }
  for (const f of absent) {
    failed++;
    console.log(`${"FAIL".padEnd(5)} ${label.padEnd(22)} ${set.table}.csv names ${f} but public/icons/${set.dir}/${f} does not exist -- it renders as a broken image`);
  }
  for (const f of orphans) {
    failed++;
    console.log(`${"FAIL".padEnd(5)} ${label.padEnd(22)} orphan: public/icons/${set.dir}/${f} ships but neither ${set.table}.csv nor src/ names it`);
  }
  for (const f of known) {
    warned++;
    console.log(`${"WARN".padEnd(5)} ${label.padEnd(22)} ${f}: known gap -- upstream has never drawn this icon (see KNOWN_GAPS)`);
  }
  if (!stale.length && !absent.length && !orphans.length) {
    console.log(`${"OK".padEnd(5)} ${label.padEnd(22)} ${onDisk.size} files, ${expected.size} names, reconciled${known.length ? ` (${known.length} known gap(s))` : ""}`);
  }
}

console.log(
  failed
    ? `\n${failed} problem(s) across ${referenced.length} referenced data sources.`
    : `\nAll ${referenced.length} data sources reachable, same-origin by construction and schema-valid from ${appOrigin}.` +
      (warned
        ? `\nEvery icon they name ships except ${warned} known gap(s) above -- upstream has never drawn those.`
        : `\nEvery icon they name ships.`)
);
process.exit(failed ? 1 : 0);
