# Containerised Dev Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `mx-division-builds` fork into a self-contained, containerised base the owner can modify freely — no third-party runtime dependency, a modern toolchain, and a real test suite — without installing anything on the host.

**Architecture:** Four phases, each leaving working software. A container comes first so every later command runs inside it. Then the app is cut loose from `buildstation.app` by serving the local data snapshot from `public/data/`, which also removes the dev-only CORS proxy and makes production builds work for the first time. Then the toolchain moves to Vite by reviving `origin/migrate-to-vite`. Finally Vitest lands with real unit tests on the damage math.

**Tech Stack:** Docker + Compose · Node 22 (in-container) · Vite 5 · Vue 2.7 (`@vitejs/plugin-vue2`) · Vitest · PapaParse · RxJS 7

## Global Constraints

- **Nothing new on the host OS.** Every `npm`/`node`/`vitest` invocation runs via `docker compose`. The host already has Docker; it must not gain Node tooling. The existing host-installed `node_modules/` is removed in Task 1.4.
- **Tailnet-only exposure.** The dev server publishes to the host's `tailscale0` address (shown as `<TAILNET_IP>`) and never `0.0.0.0`. This copies the `immich-ml` precedent in `/srv/nebula/docker/services/immichml/immichml.yml`.
- **Branch workflow.** All work continues on `chore/runnable-on-node-24` or a successor branch. Never commit to `master`. Do not push to the public GitHub fork without the owner's explicit go-ahead.
- **No internal addresses in tracked files.** This fork is **public** on GitHub. `<TAILNET_IP>` and `<PUBLIC_IP>` are placeholders throughout this document — the real values are never written to the repo. `scripts/dev.sh` derives the tailnet address from the `tailscale0` interface at runtime; the values are recorded in the private Obsidian vault under `MX Division Builds`.
- **No secrets in the repo.** `.env.local` stays gitignored. Nothing in this plan introduces a credential.
- **Data snapshot is the source of truth** for game data: `/srv/project/data/td2-reference/2026-08-22/`. Do not re-fetch from `buildstation.app` at runtime after Phase 2.
- **Node 22** in the container. Vite 5 requires ≥18; 22 is current LTS and avoids the md4/OpenSSL-3 problem entirely.

---

## Finding that shaped this plan — read before starting

I previously called `origin/migrate-to-vite` "badly stale, 339 commits behind". **That was misleading and it changes the recommendation.** Measured:

```
git rev-list --count $(git merge-base master origin/migrate-to-vite)..master
  → 339 commits

git rev-list --count $(git merge-base ...)..master -- src/ package.json vue.config.js public/index.html
  → 5 commits
```

**334 of the 339 are the bot updating `public/vendors/*.json`.** Only **5** touch source. The branch is five commits behind in any sense that matters, and Phase 3 is a tractable rebase rather than a rewrite.

The five, and what to do with each:

| Commit | Files | Carry? |
|---|---|---|
| `f157214` Expertise max 30, arrows beyond | `GearSlot.vue` `WeaponSlot.vue` `SkillSlot.vue` `classes.js` | **Yes** — real behaviour |
| `d9b0218` Aegis/Palisade icons + named-attribute logic | `GearSlot.vue` `GearSelectionModal.vue` | **Yes** — real behaviour |
| `68b1864` Y7S1 patchnotes | `VersionModal.vue` | Yes — trivial, no conflict |
| `e684e0a` guard empty vendor files | `dataImporter.js` | **No** — Phase 2 removes the vendor fetch |
| `9a033d9` vendor fix | `dataImporter.js` | **No** — same |

Conflict surface: 6 of those 7 files were also rewritten on the Vite branch. `WeaponSlot.vue` is the sharp one (master changed 276 lines, Vite changed 446). Budget the care there.

---

## File Structure

| Path | Responsibility | Phase |
|---|---|---|
| `Dockerfile.dev` | Node 22 dev image; deps installed inside, never on host | 1 |
| `docker-compose.dev.yml` | Dev server + one-shot test/lint runner; tailnet bind | 1 |
| `.dockerignore` | Keep `node_modules`, `dist*`, `.git` out of build context | 1 |
| `docs/dev.md` | How to run everything through the container | 1 |
| `public/data/*.csv` | The 20 reference tables, served locally | 2 |
| `scripts/import-snapshot.mjs` | Copies a dated snapshot into `public/data/` | 2 |
| `scripts/check-data-sources.mjs` | Existing 3-layer check; updated for local paths | 2 |
| `vite.config.ts` | Vite build + dev server config | 3 |
| `vitest.config.ts` | Test runner config | 4 |
| `src/utils/statsService.spec.js` | Unit tests for the damage math | 4 |

---

## Phase 1 — Container

### Task 1.1: Dev image and compose file

**Files:**
- Create: `Dockerfile.dev`
- Create: `docker-compose.dev.yml`
- Create: `.dockerignore`
- Create: `scripts/dev.sh` (executable)
- Modify: `.gitignore`

**Interfaces:**
- Produces: service names `app` (long-running dev server) and `tools` (one-shot runner). Later tasks invoke `./scripts/dev.sh run --rm tools <cmd>`.

- [ ] **Step 1: Write `.dockerignore`**

```
node_modules
dist
dist-dev
dist-staging
.git
.env.local
docs
```

- [ ] **Step 2: Write `Dockerfile.dev`**

`node_modules` is deliberately NOT copied in; it lives in a named volume so the host bind-mount cannot shadow it and the host never needs it.

```dockerfile
# Dev-only image. Deps are installed into a named volume at first run,
# so the host never needs Node installed and a bind-mounted source tree
# cannot shadow node_modules.
FROM node:22-alpine

WORKDIR /app

# git: the version modal and some tooling shell out to it.
RUN apk add --no-cache git

# Non-root, matching the host uid so bind-mounted files stay writable.
ARG UID=1000
ARG GID=1000
RUN deluser --remove-home node \
 && addgroup -g ${GID} app \
 && adduser -u ${UID} -G app -s /bin/sh -D app

USER app
EXPOSE 8090
CMD ["npm", "run", "serve"]
```

- [ ] **Step 3: Write `docker-compose.dev.yml`**

```yaml
# Dev/test only. Not part of the /srv/nebula stack -- this fork has not been
# adopted as a service, so it stays self-contained in its own repo.
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile.dev
      args:
        UID: ${UID:-1000}
        GID: ${GID:-1000}
    container_name: mxdiv-dev
    init: true
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    volumes:
      - .:/app
      - node_modules:/app/node_modules
    env_file:
      - .env.local
    environment:
      # Bind inside the container; the tailnet restriction is done by the
      # published-port address below, not by the app.
      DEV_HOST: 0.0.0.0
      DEV_PORT: 8090
    ports:
      # Tailnet-only, same pattern as immich-ml. NEVER 0.0.0.0.
      # `:?` on purpose: an unset DEV_BIND_IP makes compose substitute an
      # empty string and ":8090:8090" binds EVERY interface -- publishing the
      # dev server publicly. Hard-fail instead.
      - "${DEV_BIND_IP:?DEV_BIND_IP unset - use ./scripts/dev.sh; refusing to bind all interfaces}:8090:8090"

  # One-shot runner: npm/vitest/lint without touching the host.
  tools:
    build:
      context: .
      dockerfile: Dockerfile.dev
      args:
        UID: ${UID:-1000}
        GID: ${GID:-1000}
    profiles: ["tools"]
    volumes:
      - .:/app
      - node_modules:/app/node_modules
    env_file:
      - .env.local
    entrypoint: []
    command: ["node", "--version"]

volumes:
  node_modules:
```

- [ ] **Step 4: Write `scripts/dev.sh` — derive the bind address, don't store it**

> **`env_file:` does not feed compose interpolation.** It sets variables
> *inside the container*; `${...}` in the compose file resolves from the shell
> or a file named exactly `.env`. Verified: with `MYVAR` defined only in
> `.env.local`, `docker compose config` rendered `${MYVAR:-default}` as the
> default. So `DEV_BIND_IP` must reach compose through the shell.
>
> A wrapper beats a committed `.env` here: the tailnet address is *derivable*
> from the interface, so the repo never has to contain an internal IP at all —
> which matters because this fork is public. It also fails loudly when
> Tailscale is down instead of falling back to something reachable.

```bash
#!/usr/bin/env sh
# Wrapper around docker compose for the dev stack.
#
# Exists because compose interpolates ${DEV_BIND_IP} from the shell, not from
# env_file:. Rather than storing the host's tailnet address in a file, derive
# it from the interface -- the repo is public and should contain no internal
# addresses, and a missing interface should be a loud failure, not a silent
# fallback to something publicly reachable.
#
# Usage: ./scripts/dev.sh up -d app
#        ./scripts/dev.sh --profile tools run --rm tools npm test
set -eu

DEV_BIND_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"

if [ -z "${DEV_BIND_IP}" ]; then
  echo "error: no IPv4 address on tailscale0 - is Tailscale up?" >&2
  echo "       refusing to start: without it the port would bind every interface." >&2
  exit 1
fi

UID_="$(id -u)"
GID_="$(id -g)"

export DEV_BIND_IP
export UID="${UID_}"
export GID="${GID_}"

exec docker compose -f "$(dirname "$0")/../docker-compose.dev.yml" "$@"
```

Make it executable: `chmod +x scripts/dev.sh`

- [ ] **Step 4b: Prove the interpolation resolves before building**

Run: `./scripts/dev.sh config | grep -A3 published`
Expected: `host_ip: <TAILNET_IP>` and `published: "8090"`.
If it errors with "refusing to bind all interfaces", the wrapper is not exporting — fix that rather than removing the guard.

- [ ] **Step 5: Build the image**

Run: `./scripts/dev.sh build app`
Expected: build succeeds, ends with `naming to docker.io/library/mx-division-builds-app`.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.dev docker-compose.dev.yml .dockerignore
git commit -m "feat: containerise the dev server so the host needs no Node"
```

### Task 1.2: Install dependencies inside the container

**Files:**
- Modify: none (populates the `node_modules` volume)

- [ ] **Step 1: Install into the volume**

Run: `./scripts/dev.sh --profile tools run --rm tools npm ci`
Expected: `added NNNN packages`. If `npm ci` fails because the lockfile drifted, use `npm install` and commit the resulting `package-lock.json`.

- [ ] **Step 2: Verify the toolchain is in the container, not on the host**

Run: `./scripts/dev.sh --profile tools run --rm tools node -e "console.log(process.version, require('vue/package.json').version)"`
Expected: `v22.x.x 2.6.11`

- [ ] **Step 3: Commit any lockfile change**

```bash
git add package-lock.json
git commit -m "chore: lockfile as resolved inside the container" || echo "no change"
```

### Task 1.3: Bring the containerised server up and verify it is tailnet-only

**Files:**
- Modify: none

- [ ] **Step 1: Stop the host-run dev server**

```bash
for p in $(pgrep -f "cli-service"); do
  c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  case "$c" in *bash*|*pgrep*) ;; *) kill "$p";; esac
done
```

- [ ] **Step 2: Start the container**

Run: `./scripts/dev.sh up -d app`
Then: `./scripts/dev.sh logs -f app` until `Compiled successfully`.

- [ ] **Step 3: Verify tailnet-only binding**

```bash
curl -s -o /dev/null -w "tailnet %{http_code}\n" http://<TAILNET_IP>:8090/
curl -s -m 4 -o /dev/null -w "public  %{http_code}\n" http://<PUBLIC_IP>:8090/ || echo "public refused (correct)"
docker port mxdiv-dev
```
Expected: tailnet `200`; public refused; `docker port` shows `8090/tcp -> <TAILNET_IP>:8090`.

- [ ] **Step 4: Verify data still loads**

Run: `./scripts/dev.sh --profile tools run --rm tools npm run check`
Expected: `All 20 data sources reachable, CORS-clear and schema-valid`.

> The check resolves relative URLs against the app origin. Pass the origin explicitly if it defaults wrong:
> `... npm run check http://<TAILNET_IP>:8090`

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "chore: verify containerised dev server is tailnet-only"
```

### Task 1.4: Remove host Node artefacts and document the workflow

**Files:**
- Create: `docs/dev.md`
- Delete: `node_modules/` (host copy only)
- **Do NOT delete `.npmrc`** — see below

`.npmrc` existed only to re-enable OpenSSL's legacy provider for webpack 4's md4 hashing. The container runs Node 22 and Phase 3 removes webpack entirely — but until Phase 3 lands, webpack 4 is still in use, so **`.npmrc` must stay until Task 3.4**. Delete only the host `node_modules`.

- [ ] **Step 1: Remove the host-installed dependencies**

```bash
rm -rf /srv/project/Web/mx-division-builds/node_modules
```
Verify the container is unaffected: `./scripts/dev.sh --profile tools run --rm tools node -e "require('vue')"` → no output, exit 0.

- [ ] **Step 2: Write `docs/dev.md`**

````markdown
# Development

Everything runs in Docker. The host needs Docker and nothing else — no Node,
no npm, no yarn.

## Start the dev server

```sh
./scripts/dev.sh up -d app
./scripts/dev.sh logs -f app
```

Serves on `http://<TAILNET_IP>:8090/` — tailnet-only. Hot reload is on;
edit a file and the browser updates.

## Run anything else

```sh
alias mxrun='./scripts/dev.sh --profile tools run --rm tools'

mxrun npm run check      # data sources: reachable, CORS-clear, schema-valid
mxrun npm run lint
mxrun npm test
mxrun npm install <pkg>
```

## Config

`.env.local` is gitignored and holds the data URLs. The dev-server bind address
is not stored anywhere -- `scripts/dev.sh` derives it from `tailscale0`. `.env.local` does
not survive a fresh clone — the values are documented in the Obsidian vault
under `MX Division Builds`.
````

- [ ] **Step 3: Commit**

```bash
git add docs/dev.md
git commit -m "docs: how to run everything through the container"
```

---

## Phase 2 — Cut the upstream data ties

After this phase the app has no runtime dependency on `buildstation.app`, no CORS proxy, and a production build that actually works.

### Task 2.1: Import the snapshot into `public/data/`

**Files:**
- Create: `scripts/import-snapshot.mjs`
- Create: `public/data/*.csv` (20 files)

**Interfaces:**
- Produces: `public/data/<endpoint>.csv` for each of the 20 endpoint names (`mask`, `chest`, `gloves`, `holster`, `kneepads`, `backpack`, `gearAttributes`, `gearMods`, `gearTalents`, `brandsetBonuses`, `brands`, `statsMapping`, `weapon`, `weaponAttributes`, `weaponMods`, `weaponTalents`, `skill`, `skillStats`, `skillMods`, `specialization`), plus `public/data/SNAPSHOT.txt` recording provenance.

- [ ] **Step 1: Write the importer**

```javascript
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
```

- [ ] **Step 2: Run it**

Run: `./scripts/dev.sh --profile tools run --rm -v /srv/project/data:/data:ro tools node scripts/import-snapshot.mjs /data/td2-reference/2026-08-22`
Expected: `20 tables -> public/data/`

- [ ] **Step 3: Commit**

```bash
git add scripts/import-snapshot.mjs public/data
git commit -m "feat: serve the data snapshot locally instead of buildstation.app"
```

### Task 2.2: Repoint the app and delete the proxy

> **Plan change — why this no longer uses env vars.**
> A global deny rule on `**/.env*` makes `.env.local` unwritable by any agent,
> including the controller. But the better answer was hiding behind it: those
> twenty `VUE_APP_DATA_URL_*` vars existed because upstream fetched from a
> remote API whose URL could change. The data is now a **static asset shipped
> in the repo at a known path**, so the indirection buys nothing — and it is
> the exact indirection that made this repo unrunnable as cloned (upstream
> never committed a `.env`, so a fresh clone rendered a blank app).
> Removing them means **the app works from a fresh clone with no config at
> all**, which is the whole point of Phase 2.

**Files:**
- Modify: `src/utils/dataImporter.js` (the four `*Source` arrays + one helper)
- Modify: `vue.config.js` (remove `devServer.proxy`)
- Modify: `scripts/check-data-sources.mjs` (derive the table list from disk)

**Interfaces:**
- Produces: `dataUrl(name)` in `dataImporter.js`, returning `` `${getAppRootPath()}data/${name}.csv` ``. Phase 3 keeps this helper; only `getAppRootPath()`'s internals change under Vite.

- [ ] **Step 1: Add the helper to `src/utils/dataImporter.js`**

Immediately after the existing imports:

```javascript
// Data ships with the app as static CSV under public/data/. It used to come
// from a third-party API behind twenty VUE_APP_DATA_URL_* env vars; that
// indirection is why a fresh clone rendered a blank app, since upstream never
// committed the .env those vars lived in. A path built from the table name
// needs no configuration and cannot be misconfigured.
const dataUrl = (name) => `${getAppRootPath()}data/${name}.csv`;
```

- [ ] **Step 2: Replace every `process.env.VUE_APP_DATA_URL_*` with a `dataUrl()` call**

Exact mapping — the left side is the existing `url:` value, the right side replaces it:

| Replace | With |
|---|---|
| `process.env.VUE_APP_DATA_URL_MASK` | `dataUrl("mask")` |
| `process.env.VUE_APP_DATA_URL_CHEST` | `dataUrl("chest")` |
| `process.env.VUE_APP_DATA_URL_GLOVES` | `dataUrl("gloves")` |
| `process.env.VUE_APP_DATA_URL_HOLSTER` | `dataUrl("holster")` |
| `process.env.VUE_APP_DATA_URL_KNEEPADS` | `dataUrl("kneepads")` |
| `process.env.VUE_APP_DATA_URL_BACKPACK` | `dataUrl("backpack")` |
| `process.env.VUE_APP_DATA_URL_GEAR_ATTRIBUTES` | `dataUrl("gearAttributes")` |
| `process.env.VUE_APP_DATA_URL_GEAR_MODS` | `dataUrl("gearMods")` |
| `process.env.VUE_APP_DATA_URL_GEAR_TALENTS` | `dataUrl("gearTalents")` |
| `process.env.VUE_APP_DATA_URL_BRAND_SET_BONUSES` | `dataUrl("brandsetBonuses")` |
| `process.env.VUE_APP_DATA_URL_BRANDS_DATA` | `dataUrl("brands")` |
| `process.env.VUE_APP_DATA_URL_STATS_MAPPING` | `dataUrl("statsMapping")` |
| `process.env.VUE_APP_DATA_URL_WEAPONS` | `dataUrl("weapon")` |
| `process.env.VUE_APP_DATA_URL_WEAPON_ATTRIBUTES` | `dataUrl("weaponAttributes")` |
| `process.env.VUE_APP_DATA_URL_WEAPON_MODS` | `dataUrl("weaponMods")` |
| `process.env.VUE_APP_DATA_URL_WEAPON_TALENTS` | `dataUrl("weaponTalents")` |
| `process.env.VUE_APP_DATA_URL_SKILLS` | `dataUrl("skill")` |
| `process.env.VUE_APP_DATA_URL_SKILL_STATS` | `dataUrl("skillStats")` |
| `process.env.VUE_APP_DATA_URL_SKILL_MODS` | `dataUrl("skillMods")` |
| `process.env.VUE_APP_DATA_URL_SPECIALIZATION` | `dataUrl("specialization")` |

Note the three that are NOT a straight lowercase of the var name: `WEAPONS`→`weapon`, `SKILLS`→`skill`, `BRANDS_DATA`→`brands`. Get these wrong and those tables 404 while the rest load.

Leave `process.env.VUE_APP_DB_VERSION` alone — `public/DB.Version` is fetched at runtime and is a separate mechanism.

- [ ] **Step 3: Verify no data URL env var survives**

Run: `grep -rn "VUE_APP_DATA_URL" src/ || echo "clean"`
Expected: `clean`

- [ ] **Step 4: Remove the proxy block from `vue.config.js`**

Delete the whole `proxy: { "/td2data": {...} }` key and its comment, leaving:

```javascript
	devServer: {
		host: process.env.DEV_HOST || "localhost",
		port: Number(process.env.DEV_PORT) || 8080,
		disableHostCheck: true,
	},
```

- [ ] **Step 5: Point the checker at the shipped files instead of env vars**

In `scripts/check-data-sources.mjs`, replace the `.env.local` parsing and the
`dataUrls` derivation with a listing of what actually ships. Replace the block
that reads `.env.local` and builds `env`/`dataUrls` with:

```javascript
import { readdirSync } from "fs";

// Derive the table list from what actually ships, not from config. This
// checks the real artifact and cannot drift from it.
const dataDir = join(root, "public", "data");
const dataUrls = readdirSync(dataDir)
  .filter((f) => f.endsWith(".csv"))
  .sort()
  .map((f) => [f.replace(/\.csv$/, ""), `data/${f}`]);

const origin = process.argv[2] || "http://localhost:8090";
```

Keep the three checking layers and the `CONTRACTS` map unchanged, but key
`CONTRACTS` by the **file** name rather than the env-var suffix:

```javascript
const GEAR = ["Quality", "Item Name", "Brand", "Core", "Attribute 1", "Attribute 2", "Mod", "Talent"];
const CONTRACTS = {
  mask: GEAR, chest: GEAR, gloves: GEAR, holster: GEAR, kneepads: GEAR, backpack: GEAR,
  weapon: ["Name", "Quality", "RPM", "Base Damage", "Mag Size", "Optimal Range",
           "Reload Speed (ms)", "HSD", "Core 1", "Core 1 Max", "Core 2", "Core 2 Max",
           "Weapon Type", "Variant", "Talent", "Optics", "Under Barrel", "Magazine", "Muzzle"],
  skill: ["Skill ID", "Item Name", "Icon", "Variant", "Quality", "Expertise Bonus",
          "Slot One", "Slot Two", "Slot Three", "Mod 1", "Mod 2", "Mod 3", "Desc"],
};
```

and in the loop use the file name directly as `name` (drop the
`key.replace("VUE_APP_DATA_URL_", "")`).

- [ ] **Step 6: Restart and verify**

```bash
./scripts/dev.sh restart app
./scripts/dev.sh --profile tools run --rm tools npm run check http://<the tailnet ip>:8090
```
Expected: 20/20 OK, every row `same-origin`, the 8 with contracts reporting `contract ok`.

- [ ] **Step 7: Verify a production build works — the thing that was impossible before**

```bash
./scripts/dev.sh --profile tools run --rm tools npm run build-prod
./scripts/dev.sh --profile tools run --rm tools sh -c 'ls dist/data/*.csv | wc -l'
```
Expected: build succeeds; `20`.

- [ ] **Step 8: Prove a fresh clone needs no config**

This is the real acceptance test for Phase 2. Build from a pristine copy of
the tracked tree, with no `.env.local` present:

```bash
./scripts/dev.sh --profile tools run --rm tools sh -c '
  rm -rf /tmp/fresh && git clone -q --no-hardlinks /app /tmp/fresh &&
  cd /tmp/fresh && ls -a | grep -c "^\.env" || echo "no env files: correct"'
```
Expected: `no env files: correct` — confirming the tracked tree carries no
env file, and (with Step 6 passing) that none is needed.

- [ ] **Step 9: Commit**

```bash
git add src/utils/dataImporter.js vue.config.js scripts/check-data-sources.mjs
git commit -m "feat: serve data from public/data and drop the URL env vars

The twenty VUE_APP_DATA_URL_* vars pointed at a third-party API. With the
tables shipped in public/data/ the indirection buys nothing, and it was
actively harmful: upstream never committed the .env those vars lived in, so
a fresh clone built fine and rendered a blank app.

Paths are now derived from the table name. The dev-only CORS proxy goes with
them -- it existed solely to tunnel past the provider's origin allowlist, and
being dev-server-only it meant no production build could ever load data."
```

### Task 2.3: Strip upstream's analytics and deploy target

**Files:**
- Modify: `public/index.html:9-24` (GTM block + noscript)
- Delete: `scripts/gh-pages-deploy.js`
- Modify: `package.json` (drop `gh-pages-deploy` script)

- [ ] **Step 1: Remove the Google Tag Manager block**

Delete the `<script>(function(w,d,s,l,i){...})(window,document,"script","dataLayer","GTM-NP54972");</script>` block from `public/index.html`, and the matching `<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-NP54972"...></noscript>` if present.

Verify: `grep -c GTM public/index.html` → `0`

- [ ] **Step 2: Remove the gh-pages deploy path**

```bash
git rm scripts/gh-pages-deploy.js
node -e '
const fs=require("fs");
const p=JSON.parse(fs.readFileSync("package.json","utf8"));
delete p.scripts["gh-pages-deploy"];
fs.writeFileSync("package.json", JSON.stringify(p,null,"\t")+"\n");
'
```

> `execa` is a devDependency used only by that script. Leave it for now —
> Phase 3 replaces `package.json` wholesale.

- [ ] **Step 3: Verify the build still works**

Run: `./scripts/dev.sh --profile tools run --rm tools npm run build-prod`
Expected: succeeds. Then `grep -rc GTM dist/index.html` → `0`.

- [ ] **Step 4: Commit**

```bash
git add public/index.html package.json
git commit -m "chore: remove upstream's GTM container and gh-pages deploy

GTM-NP54972 is mxswat's analytics property; every visitor to a self-hosted
copy would be reported into a third party's account. gh-pages-deploy.js
force-pushes to a branch of the upstream repo's deploy target."
```

---

## Phase 3 — Vite

Riskiest phase. It runs against an app that is already self-contained, so nothing here has to preserve proxy or CORS behaviour.

### Task 3.1: Create the integration branch

- [ ] **Step 1: Branch from the Vite work**

```bash
git branch vite-base origin/migrate-to-vite
git checkout vite-base
git log --oneline -3
```

- [ ] **Step 2: Confirm what is missing relative to master**

```bash
git log --oneline $(git merge-base master origin/migrate-to-vite)..master \
  -- src/ package.json vue.config.js public/index.html
```
Expected: exactly the 5 commits listed in the finding above.

### Task 3.2: Carry the three behavioural commits

**Files:**
- Modify: `src/components/GearSlot.vue`, `src/components/WeaponSlot.vue`, `src/components/SkillSlot.vue`, `src/components/Modals/GearSelectionModal.vue`, `src/components/Modals/VersionModal.vue`, `src/utils/classes.js`

- [ ] **Step 1: Cherry-pick the trivial one first**

```bash
git cherry-pick 68b1864   # Y7S1 patchnotes, VersionModal only
```
Expected: clean.

- [ ] **Step 2: Cherry-pick the named-attribute commit**

```bash
git cherry-pick d9b0218
```
If it conflicts in `GearSlot.vue` / `GearSelectionModal.vue`, resolve by keeping the **Vite branch's structure** and re-applying master's *logic* (the named-attribute selection branch and the Aegis/Palisade icon entries). Then:
```bash
git add -A && git cherry-pick --continue
```

- [ ] **Step 3: Cherry-pick the expertise commit — the sharp one**

```bash
git cherry-pick f157214
```
`WeaponSlot.vue` will conflict (master +276 / Vite +446 lines). Resolve by keeping the Vite branch's template and PrimeVue components, and porting master's change: expertise `max` becomes 30 while the input's arrows are allowed past it. `classes.js` sets `this["expertise"].max = 30` for both `WeaponBase` and `SkillBase`.

- [ ] **Step 4: Verify it builds**

```bash
./scripts/dev.sh --profile tools run --rm tools npm install
./scripts/dev.sh --profile tools run --rm tools npm run build-prod
```
Expected: build succeeds.

- [ ] **Step 5: Commit**

Cherry-picks commit themselves. Verify with `git log --oneline -4`.

### Task 3.3: Reapply Phase 1 and 2 work onto the Vite branch

**Files:**
- Modify: `vite.config.ts`, `index.html`, `package.json`
- Create: `Dockerfile.dev`, `docker-compose.dev.yml`, `.dockerignore`, `docs/dev.md`, `scripts/dev.sh`, `scripts/import-snapshot.mjs`, `scripts/check-data-sources.mjs`, `public/data/*.csv`, `public/DB.Version`, `src/utils/dataImporter.js`

The Vite branch predates all of Phase 1 and 2, so those files must be brought across.

> **REWRITTEN 2026-08-22 after the Phase 3 pre-flight scan.** The original
> Steps 1–3 were written before Task 2.2's approved deviation (deleting the
> twenty `VUE_APP_DATA_URL_*` vars). They renamed env vars that no longer
> exist, in a `.env.local` no agent can write, and their file list omitted
> four things — `scripts/dev.sh`, `src/utils/dataImporter.js`, `index.html`
> and `public/DB.Version` — whose omission would have reintroduced the blank
> app and the stale-cache bug (Phase 2 review finding C2) verbatim. Carry the
> work across as three readable commits, not one blob.

- [ ] **Step 1: Carry the container layer — commit 1 of 3**

`scripts/dev.sh` is the one every later step invokes, and it does not exist on
the Vite branch.

```bash
git checkout chore/runnable-on-node-24 -- \
  Dockerfile.dev docker-compose.dev.yml .dockerignore docs/dev.md scripts/dev.sh
test -x scripts/dev.sh || chmod +x scripts/dev.sh
git add -A && git commit -m "feat: carry the containerised dev server onto the Vite base"
```

- [ ] **Step 2: Carry the local-data layer — commit 2 of 3**

`src/utils/dataImporter.js` is the critical one. The Vite branch's copy reads
twenty `import.meta.env.VITE_APP_DATA_URL_*` vars that Phase 2 deleted; leave
it in place and every table resolves to `undefined` and the app renders blank.
Phase 2's version derives each path from the table name and needs no config.

```bash
git checkout chore/runnable-on-node-24 -- \
  public/data public/DB.Version src/utils/dataImporter.js \
  scripts/import-snapshot.mjs scripts/check-data-sources.mjs
grep -c "import.meta.env" src/utils/dataImporter.js   # expect 0
cat public/DB.Version                                  # expect 26.0-mdb
git add -A && git commit -m "feat: serve the local data snapshot on the Vite base"
```

Then delete the dead `process.env.VUE_APP_DB_VERSION` read at
`src/utils/dataImporter.js:22` — it is a standing minor and `process.env` does
not exist in a Vite browser bundle, so leaving it is now a runtime error, not
just dead weight. `RemoteDBVersion` must come from the fetched
`public/DB.Version`, matching what Phase 2's C2 fix established.

- [ ] **Step 3: Port the upstream-cleanup into the root `index.html` — commit 3 of 3**

**Vite moves `index.html` to the repo root.** Do not copy `public/index.html`
across — the Vite branch deletes that path. Instead apply Task 2.3's removals
and the Phase 2 C1 fix to the Vite branch's root `index.html`:

- remove the Google Tag Manager script block and its `<noscript>` iframe
- remove the `google-site-verification` meta
- replace `%VITE_APP_TITLE%` in the `og:site_name` / `og:title` tags with the
  literal title, so a fresh clone with no env file still renders correct tags
- point `og:*` at this fork, not `mxswat`

```bash
grep -Ec "googletagmanager|google-site-verification|%VITE_APP_TITLE%|mxswat" index.html   # expect 0
git add -A && git commit -m "chore: strip upstream analytics and env-dependent tags from index.html"
```

- [ ] **Step 4: Point the dev server at the tailnet in `vite.config.ts`**

```typescript
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue2'

export default ({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return defineConfig({
    plugins: [vue()],
    base: env.BASE_URL,
    server: {
      // Bind inside the container; the compose published-port address is
      // what restricts reachability to the tailnet.
      host: env.DEV_HOST || 'localhost',
      port: Number(env.DEV_PORT) || 8090,
      strictPort: true,
    },
  });
}
```

- [ ] **Step 5: Point the container at Vite's dev script**

In `docker-compose.dev.yml` the `app` service command becomes `npm run dev`; add to `package.json` scripts:
```json
"dev": "vite",
"check": "node scripts/check-data-sources.mjs"
```

- [ ] **Step 6: Rebuild and verify end to end**

```bash
./scripts/dev.sh build app
./scripts/dev.sh --profile tools run --rm tools npm install
./scripts/dev.sh up -d app
./scripts/dev.sh --profile tools run --rm tools npm run check
curl -s -o /dev/null -w "%{http_code}\n" http://<TAILNET_IP>:8090/
```
Expected: `npm run check` 20/20 OK; curl `200`.

- [ ] **Step 7: Open the app in a browser and confirm gear renders**

The owner must confirm from their own workstation. **A 200 and a green check do not prove the app works** — that exact combination held while the app showed nothing but an error screen. Ask for confirmation that the inventory populates.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: run the Vite build in the container against local data"
```

### Task 3.4: Delete the webpack-era workarounds

**Files:**
- Delete: `.browserslistrc`, `yarn.lock` (`vue.config.js`, `.npmrc`, `babel.config.js` are absent on the Vite base by construction)

- [ ] **Step 1: Remove them**

Of the four, **only `.browserslistrc` exists on the Vite base** — `vue.config.js`,
`.npmrc` and `babel.config.js` were never on that branch and Task 3.3
deliberately does not carry them, so `git rm` on all four fails hard.

```bash
git rm .browserslistrc
git rm -f yarn.lock          # Vite branch ships yarn.lock; yarn is installed nowhere
ls vue.config.js .npmrc babel.config.js 2>&1   # expect: No such file (x3)
```

`.npmrc` held `node-options=--openssl-legacy-provider`, needed only because webpack 4 hashes with md4. Vite uses esbuild/rollup and never calls it — which is why it never had to come across.

- [ ] **Step 2: Verify a clean build from scratch**

```bash
./scripts/dev.sh down
docker volume rm mx-division-builds_node_modules
./scripts/dev.sh --profile tools run --rm tools npm install
./scripts/dev.sh --profile tools run --rm tools npm run build-prod
```
Expected: install and build both succeed with no `.npmrc` present.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: drop vue-cli, babel and the md4 workaround

Vite replaces all of it. .npmrc existed only to re-enable OpenSSL's legacy
provider for webpack 4's md4 hashing."
```

---

## Phase 4 — Vitest and the damage math

### Task 4.1: Install and wire the runner

**Files:**
- Create: `vitest.config.ts`
- Modify: `package.json`

**Interfaces:**
- Produces: `npm test` (single run) and `npm run test:watch`.

- [ ] **Step 1: Add the dependency inside the container**

Run: `./scripts/dev.sh --profile tools run --rm tools npm install -D vitest`

- [ ] **Step 2: Write `vitest.config.ts`**

```typescript
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // node, not jsdom: the units under test are pure math. Adding jsdom
    // would pull a DOM in for no reason -- add it when a component test
    // actually needs one.
    environment: 'node',
    include: ['src/**/*.spec.js'],
  },
})
```

- [ ] **Step 3: Add the scripts**

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 4: Verify the runner starts**

Run: `./scripts/dev.sh --profile tools run --rm tools npm test`
Expected: `No test files found` — the runner works, there is nothing to run yet.

- [ ] **Step 5: Commit**

```bash
git add vitest.config.ts package.json package-lock.json
git commit -m "test: add vitest"
```

### Task 4.2: First failing test — the string/number inconsistency

**Files:**
- Create: `src/utils/statsService.spec.js`

**Interfaces:**
- Consumes: `statsService` default export from `src/utils/statsService.js`.

> **Import-time landmine:** `statsService.js` imports `./dataImporter`, which fires
> ~21 network requests and touches `localStorage` at module load. In Node both
> explode. The test must mock that module — this is why the first test needs a
> `vi.mock` and is not a one-liner.

- [ ] **Step 1: Write the failing test**

```javascript
import { describe, it, expect, vi } from "vitest";

// dataImporter fetches 20 CSVs and reads localStorage at import time.
// statsService imports it, so without this mock the suite cannot even load.
vi.mock("./dataImporter", () => ({
  gearData: {},
  skillsData: {},
  weaponsData: {},
  specializationList: {},
  IsEverythingLoadedPromise: Promise.resolve(),
  VendorData: Promise.resolve({ Gear: {}, Weapons: [] }),
}));

import statsService from "./statsService";

describe("flatWeaponDamage", () => {
  it("applies additive damage percentages to base damage", () => {
    // 1000 base, +10% AWD, +5% weapon-type, +0% generic => 1150
    expect(statsService.flatWeaponDamage(1000, 10, 5, 0)).toBe(1150);
  });

  it("returns a number, not a string", () => {
    // .toFixed(0) returns a string. Every caller multiplies the result, so JS
    // coerces and the bug stays invisible -- until something uses + and gets
    // "1150100" instead of 1250.
    expect(typeof statsService.flatWeaponDamage(1000, 0, 0, 0)).toBe("number");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./scripts/dev.sh --profile tools run --rm tools npm test`
Expected: both fail — `expected '1150' to be 1150` and `expected 'string' to be 'number'`.

- [ ] **Step 3: Fix the source**

In `src/utils/statsService.js`, wrap the return in `Number()` to match its sibling `calcDmgToArmored`:

```javascript
	flatWeaponDamage(
		weaponBaseDamage,
		AWD,
		weaponSpecificDamage,
		genericWeaponDamage
	) {
		return Number(
			(
				weaponBaseDamage *
				(1 + (AWD + weaponSpecificDamage + genericWeaponDamage) / 100)
			).toFixed(0)
		);
	}
```

- [ ] **Step 4: Run to verify it passes**

Run: `./scripts/dev.sh --profile tools run --rm tools npm test`
Expected: `2 passed`.

- [ ] **Step 5: Check nothing downstream depended on the string**

```bash
grep -rn "flatWeaponDamage" src/
```
Confirm every call site multiplies or compares numerically. If any concatenates, that call site was already buggy — note it and stop for review.

- [ ] **Step 6: Commit**

```bash
git add src/utils/statsService.spec.js src/utils/statsService.js
git commit -m "test: cover flatWeaponDamage; return a number not a string"
```

### Task 4.3: Cover the rest of the pure math

**Files:**
- Modify: `src/utils/statsService.spec.js`

- [ ] **Step 1: Add the failing tests**

```javascript
describe("calcDmgToArmored", () => {
  it("adds the damage-to-armored percentage", () => {
    expect(statsService.calcDmgToArmored(1000, 15)).toBe(1150);
  });
  it("is a no-op at zero", () => {
    expect(statsService.calcDmgToArmored(1000, 0)).toBe(1000);
  });
});

describe("addCHDAndOrHSDOnTopOfFlatDamage", () => {
  it("ignores crit and headshot when both chances are zero", () => {
    expect(Number(statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 50, 75, 0, 0))).toBe(1000);
  });
  it("applies crit damage scaled by crit chance", () => {
    // 1000 * (1 + (60 * 0.5)/100) = 1300
    expect(Number(statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 60, 0, 0, 50))).toBe(1300);
  });
  it("applies headshot damage scaled by headshot chance", () => {
    // 1000 * (1 + (80 * 0.25)/100) = 1200
    expect(Number(statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 0, 80, 25, 0))).toBe(1200);
  });
});

describe("calcReloadSpeed", () => {
  it("divides by the modifier -- faster reload means a smaller number", () => {
    expect(statsService.calcReloadSpeed(2000, 25)).toBe(1600);
  });
  it("is a no-op at zero", () => {
    expect(statsService.calcReloadSpeed(2000, 0)).toBe(2000);
  });
});

describe("getReloadSpeedModifier", () => {
  it("returns the base stat when there is no magazine", () => {
    expect(statsService.getReloadSpeedModifier(null, 10)).toBe(10);
  });
  it("adds a positive magazine reload bonus", () => {
    expect(statsService.getReloadSpeedModifier({ pos: "Reload Speed %", valPos: "15" }, 10)).toBe(25);
  });
  it("adds a negative magazine reload penalty", () => {
    expect(statsService.getReloadSpeedModifier({ neg: "Reload Speed %", valNeg: "-20" }, 10)).toBe(-10);
  });
});

describe("getAdditionalMagSizeFromTheMagazine", () => {
  it("returns zero without a magazine", () => {
    expect(statsService.getAdditionalMagSizeFromTheMagazine(null)).toBe(0);
  });
  it("returns the extra rounds value", () => {
    expect(statsService.getAdditionalMagSizeFromTheMagazine({ pos: "Extra Rounds", valPos: "21" })).toBe(21);
  });
  it("returns zero for a magazine that grants something else", () => {
    expect(statsService.getAdditionalMagSizeFromTheMagazine({ pos: "Reload Speed %", valPos: "15" })).toBe(0);
  });
});

describe("getStatValueFromGunMods", () => {
  it("sums positive and negative mod contributions across slots", () => {
    const weapon = {
      optic: { pos: "Critical Hit Chance", valPos: "5", neg: null, valNeg: 0 },
      muzzle: { pos: null, valPos: 0, neg: "Critical Hit Chance", valNeg: "-3" },
      magazine: null,
      "under barrel": null,
    };
    expect(statsService.getStatValueFromGunMods(weapon, "Critical Hit Chance")).toBe(2);
  });
});
```

- [ ] **Step 2: Run to see which fail**

Run: `./scripts/dev.sh --profile tools run --rm tools npm test`

> Expect some of these to **pass immediately**. That is information, not a
> process failure — it is how the Heolstor `author_id` false premise got
> caught. A test that passes on first run has documented existing behaviour.
> Any that fails has found a real defect: **stop and report it rather than
> editing the test to match the code.**

- [ ] **Step 3: Fix any genuine defect, one at a time**

For each failure, confirm against the formula in `src/utils/Notes.md` before deciding whether the code or the expectation is wrong.

- [ ] **Step 4: Run the full suite**

Run: `./scripts/dev.sh --profile tools run --rm tools npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/utils/statsService.spec.js src/utils/statsService.js
git commit -m "test: cover the pure damage, reload and mod-stat helpers"
```

### Task 4.4: Wire the checks together

**Files:**
- Modify: `docs/dev.md`

- [ ] **Step 1: Document the two commands and what each proves**

Append to `docs/dev.md`:

````markdown
## Verifying

```sh
mxrun npm test       # unit tests -- the damage math, offline, no network
mxrun npm run check  # data layer -- reachable, CORS-clear, schema-valid
```

They cover different failure modes and neither substitutes for the other.
`npm run check` needs the dev server up; `npm test` does not.

**Neither proves the app renders.** A green check on both held while the app
showed nothing but an error screen — open it in a browser.
````

- [ ] **Step 2: Run both**

```bash
./scripts/dev.sh --profile tools run --rm tools npm test
./scripts/dev.sh --profile tools run --rm tools npm run check
```
Expected: both green.

- [ ] **Step 3: Commit**

```bash
git add docs/dev.md
git commit -m "docs: what each check proves, and what neither proves"
```

---

## Done when

- `./scripts/dev.sh up -d app` serves on `<TAILNET_IP>:8090`, tailnet-only, with hot reload.
- The host has no Node, no npm, no `node_modules`.
- No runtime request leaves the machine — data is served from `public/data/`.
- `npm run build-prod` produces a working artifact (it could not before).
- No `GTM-NP54972`, no `gh-pages-deploy.js`, no `buildstation.app`.
- `npm test` runs real unit tests on `statsService`.
- The owner has confirmed in a browser from their own workstation that the inventory populates.

## Deliberately not in scope

- **Vue 3.** The Vite branch targets Vue 2.7 via `@vitejs/plugin-vue2`. Vue 2 is EOL; moving to 3 means rewriting every component against a PrimeVue major. Separate project.
- **Deploying it.** No Traefik route, no Infisical project, no `/srv/nebula` service. Adding one pre-empts the still-open keep-or-drop decision.
- **Pushing to GitHub.** The fork is public. Needs the owner's explicit go-ahead.
- **Refreshing the data.** `public/data/` is a point-in-time snapshot pinned to `DB.Version` 26.0-mdb. Re-run `snapshot.mjs` then `import-snapshot.mjs` when the game updates.
