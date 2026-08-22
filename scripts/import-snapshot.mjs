#!/usr/bin/env node
/**
 * Copies a dated snapshot from the reference store into public/data/, so the
 * app serves its own data instead of depending on a third-party API at runtime.
 *
 * Also writes public/DB.Version from the snapshot manifest. That file is the
 * cache key dataImporter.js compares against localStorage's "localDBversion":
 * if it does not move, a browser that already cached the previous release
 * never re-fetches public/data/ and silently replays stale localStorage.
 *
 * Usage: node scripts/import-snapshot.mjs /path/to/snapshot-dir
 */
import {
  readdirSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  copyFileSync,
  unlinkSync,
} from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const EXPECTED_TABLES = 20;

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = process.argv[2];

if (!src) {
  console.error("usage: node scripts/import-snapshot.mjs /path/to/snapshot-dir");
  console.error("");
  console.error("The snapshot directory must contain manifest.json and csv/.");
  console.error("It is deliberately not defaulted: the path is host-specific and");
  console.error("this repo is public.");
  process.exit(1);
}

const dest = join(root, "public", "data");
const manifest = JSON.parse(readFileSync(join(src, "manifest.json"), "utf8"));

const files = readdirSync(join(src, "csv")).filter((f) => f.endsWith(".csv"));

// Guard before touching public/data/, not after: a short snapshot should be
// rejected outright rather than half-applied and then reported as a failure.
if (files.length !== EXPECTED_TABLES) {
  console.error(
    `refusing to import: expected ${EXPECTED_TABLES} tables in ${join(src, "csv")}, got ${files.length}`
  );
  process.exit(1);
}

mkdirSync(dest, { recursive: true });

// Drop CSVs the incoming snapshot does not carry, so a shrinking snapshot
// cannot leave an orphan behind for check-data-sources.mjs to trip over.
const incoming = new Set(files);
for (const f of readdirSync(dest).filter((f) => f.endsWith(".csv"))) {
  if (!incoming.has(f)) {
    unlinkSync(join(dest, f));
    console.log(`removed stale ${f}`);
  }
}

for (const f of files) {
  copyFileSync(join(src, "csv", f), join(dest, f));
  console.log(`copied ${f}`);
}

writeFileSync(
  join(dest, "SNAPSHOT.txt"),
  [
    `snapshot: ${manifest.snapshotDate}`,
    `upstream DB.Version: ${manifest.dbVersion}`,
    `source: ${manifest.source}`,
    `tables: ${files.length}`,
    ``,
    `Regenerate with the snapshot script in the reference store, then re-run`,
    `scripts/import-snapshot.mjs <snapshot-dir> to refresh these files.`,
  ].join("\n") + "\n"
);

// The cache key. Must match the snapshot or returning browsers never re-fetch.
const dbVersionPath = join(root, "public", "DB.Version");
writeFileSync(dbVersionPath, `${manifest.dbVersion}\n`);

console.log(`\n${files.length} tables -> public/data/`);
console.log(`DB.Version -> ${manifest.dbVersion}`);
