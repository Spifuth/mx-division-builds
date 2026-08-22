#!/usr/bin/env node
/**
 * Copies a dated snapshot from the reference store into public/data/, so the
 * app serves its own data instead of depending on buildstation.app at runtime.
 *
 * Usage: node scripts/import-snapshot.mjs [/path/to/snapshot-dir]
 */
import { readdirSync, readFileSync, writeFileSync, mkdirSync, copyFileSync } from "fs";
import { join, dirname, basename } from "path";
import { fileURLToPath } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = process.argv[2] || "/srv/project/data/td2-reference/2026-08-22";
const dest = join(root, "public", "data");

const manifest = JSON.parse(readFileSync(join(src, "manifest.json"), "utf8"));
mkdirSync(dest, { recursive: true });

const files = readdirSync(join(src, "csv")).filter((f) => f.endsWith(".csv"));
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
    `Regenerate with /srv/project/data/td2-reference/snapshot.mjs, then re-run`,
    `scripts/import-snapshot.mjs to refresh these files.`,
  ].join("\n") + "\n"
);

console.log(`\n${files.length} tables -> public/data/`);
if (files.length !== 20) {
  console.error(`expected 20 tables, got ${files.length}`);
  process.exit(1);
}
