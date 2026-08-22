# Containerised Dev Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `mx-division-builds` fork into a self-contained, containerised base the owner can modify freely — no third-party runtime dependency, a modern toolchain, and a real test suite — without installing anything on the host.

**Architecture:** Four phases, each leaving working software. A container comes first so every later command runs inside it. Then the app is cut loose from `buildstation.app` by serving the local data snapshot from `public/data/`, which also removes the dev-only CORS proxy and makes production builds work for the first time. Then the toolchain moves to Vite by reviving `origin/migrate-to-vite`. Finally Vitest lands with real unit tests on the damage math.

**Tech Stack:** Docker + Compose · Node 22 (in-container) · Vite 5 · Vue 2.7 (`@vitejs/plugin-vue2`) · Vitest · PapaParse · RxJS 7

## Global Constraints

- **Nothing new on the host OS.** Every `npm`/`node`/`vitest` invocation runs via `docker compose`. The host already has Docker; it must not gain Node tooling. The existing host-installed `node_modules/` is removed in Task 1.4.
- **Tailnet-only exposure.** The dev server publishes to `100.120.243.105` (the host's `tailscale0`) and never `0.0.0.0`. This copies the `immich-ml` precedent in `/srv/nebula/docker/services/immichml/immichml.yml`.
- **Branch workflow.** All work continues on `chore/runnable-on-node-24` or a successor branch. Never commit to `master`. Do not push to the public GitHub fork without the owner's explicit go-ahead.
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

**Interfaces:**
- Produces: service names `app` (long-running dev server) and `tools` (one-shot runner). Later tasks invoke `docker compose -f docker-compose.dev.yml run --rm tools <cmd>`.

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
      - "${DEV_BIND_IP:-127.0.0.1}:8090:8090"

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

- [ ] **Step 4: Add the bind IP to `.env.local`**

```bash
cat >> .env.local <<'EOF'

# --- container ---
# Tailnet IP of the host. The dev server binds 0.0.0.0 *inside* the container;
# this is what restricts it to the tailnet on the host side.
DEV_BIND_IP=100.120.243.105
UID=1000
GID=1000
EOF
```

- [ ] **Step 5: Build the image**

Run: `docker compose -f docker-compose.dev.yml build app`
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm ci`
Expected: `added NNNN packages`. If `npm ci` fails because the lockfile drifted, use `npm install` and commit the resulting `package-lock.json`.

- [ ] **Step 2: Verify the toolchain is in the container, not on the host**

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools node -e "console.log(process.version, require('vue/package.json').version)"`
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

Run: `docker compose -f docker-compose.dev.yml up -d app`
Then: `docker compose -f docker-compose.dev.yml logs -f app` until `Compiled successfully`.

- [ ] **Step 3: Verify tailnet-only binding**

```bash
curl -s -o /dev/null -w "tailnet %{http_code}\n" http://100.120.243.105:8090/
curl -s -m 4 -o /dev/null -w "public  %{http_code}\n" http://37.27.60.170:8090/ || echo "public refused (correct)"
docker port mxdiv-dev
```
Expected: tailnet `200`; public refused; `docker port` shows `8090/tcp -> 100.120.243.105:8090`.

- [ ] **Step 4: Verify data still loads**

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run check`
Expected: `All 20 data sources reachable, CORS-clear and schema-valid`.

> The check resolves relative URLs against the app origin. Pass the origin explicitly if it defaults wrong:
> `... npm run check http://100.120.243.105:8090`

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "chore: verify containerised dev server is tailnet-only"
```

### Task 1.4: Remove host Node artefacts and document the workflow

**Files:**
- Create: `docs/dev.md`
- Delete: `node_modules/` (host copy), `.npmrc`

`.npmrc` existed only to re-enable OpenSSL's legacy provider for webpack 4's md4 hashing on Node 24. The container runs Node 22 and Phase 3 removes webpack entirely — but until Phase 3 lands, webpack 4 is still in use, so **`.npmrc` must stay until Task 3.4**. Delete only the host `node_modules`.

- [ ] **Step 1: Remove the host-installed dependencies**

```bash
rm -rf /srv/project/Web/mx-division-builds/node_modules
```
Verify the container is unaffected: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools node -e "require('vue')"` → no output, exit 0.

- [ ] **Step 2: Write `docs/dev.md`**

````markdown
# Development

Everything runs in Docker. The host needs Docker and nothing else — no Node,
no npm, no yarn.

## Start the dev server

```sh
docker compose -f docker-compose.dev.yml up -d app
docker compose -f docker-compose.dev.yml logs -f app
```

Serves on `http://100.120.243.105:8090/` — tailnet-only. Hot reload is on;
edit a file and the browser updates.

## Run anything else

```sh
alias mxrun='docker compose -f docker-compose.dev.yml --profile tools run --rm tools'

mxrun npm run check      # data sources: reachable, CORS-clear, schema-valid
mxrun npm run lint
mxrun npm test
mxrun npm install <pkg>
```

## Config

`.env.local` is gitignored and holds the data URLs plus `DEV_BIND_IP`. It does
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm -v /srv/project/data:/data:ro tools node scripts/import-snapshot.mjs /data/td2-reference/2026-08-22`
Expected: `20 tables -> public/data/`

- [ ] **Step 3: Commit**

```bash
git add scripts/import-snapshot.mjs public/data
git commit -m "feat: serve the data snapshot locally instead of buildstation.app"
```

### Task 2.2: Repoint the app and delete the proxy

**Files:**
- Modify: `.env.local` (20 URL values)
- Modify: `vue.config.js` (remove `devServer.proxy`)

- [ ] **Step 1: Repoint the URLs**

```bash
sed -i 's|^\(VUE_APP_DATA_URL_[A-Z_]*\)=/td2data/\(.*\)$|\1=data/\2.csv|' .env.local
grep -E "^VUE_APP_DATA_URL" .env.local
```
Expected: every line reads e.g. `VUE_APP_DATA_URL_MASK=data/mask.csv`.

> Relative without a leading slash: `dataImporter` passes these straight to
> PapaParse, and `getAppRootPath()` already handles the base path. A leading
> slash breaks a non-root `publicPath`.

- [ ] **Step 2: Remove the proxy block from `vue.config.js`**

Delete the whole `proxy: { "/td2data": {...} }` key and its comment. The file's `devServer` becomes:

```javascript
	devServer: {
		host: process.env.DEV_HOST || "localhost",
		port: Number(process.env.DEV_PORT) || 8080,
		disableHostCheck: true,
	},
```

- [ ] **Step 3: Restart and verify**

```bash
docker compose -f docker-compose.dev.yml restart app
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run check
```
Expected: 20/20 OK, each reported `same-origin`.

- [ ] **Step 4: Verify a production build works — the thing that was impossible before**

```bash
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run build-prod
docker compose -f docker-compose.dev.yml --profile tools run --rm tools sh -c 'ls dist/data | wc -l'
```
Expected: build succeeds; `20` (or 21 with `SNAPSHOT.txt`) files under `dist/data`.

- [ ] **Step 5: Commit**

```bash
git add vue.config.js
git commit -m "feat: drop the dev-only CORS proxy; data is now local

The proxy existed solely to tunnel past buildstation.app's origin
allowlist. With the tables in public/data/ the requests are same-origin
by construction, and production builds work for the first time -- the
proxy was dev-server-only, so a built artifact could never load data."
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run build-prod`
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
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm install
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run build-prod
```
Expected: build succeeds.

- [ ] **Step 5: Commit**

Cherry-picks commit themselves. Verify with `git log --oneline -4`.

### Task 3.3: Reapply Phase 1 and 2 work onto the Vite branch

**Files:**
- Modify: `vite.config.ts`, `Dockerfile.dev`, `docker-compose.dev.yml`, `.env.local`, `public/index.html`
- Create: `public/data/*.csv`, `scripts/import-snapshot.mjs`, `scripts/check-data-sources.mjs`, `docs/dev.md`

The Vite branch predates all of Phase 1 and 2, so those files must be brought across.

- [ ] **Step 1: Bring the container and data files over**

```bash
git checkout chore/runnable-on-node-24 -- \
  Dockerfile.dev docker-compose.dev.yml .dockerignore docs/dev.md \
  scripts/import-snapshot.mjs scripts/check-data-sources.mjs public/data
```

- [ ] **Step 2: Rename the env vars — Vite uses a different prefix**

The Vite branch reads `import.meta.env.VITE_APP_*`, not `process.env.VUE_APP_*`.

```bash
sed -i 's/^VUE_APP_/VITE_APP_/' .env.local
grep -E "^VITE_APP_DATA_URL" .env.local | head -3
```
Expected: `VITE_APP_DATA_URL_MASK=data/mask.csv`

- [ ] **Step 3: Update the check script for the new prefix**

In `scripts/check-data-sources.mjs`, change both occurrences of `VUE_APP_DATA_URL_` to `VITE_APP_DATA_URL_` (the `.filter()` and the `.replace()`).

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
docker compose -f docker-compose.dev.yml build app
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm install
docker compose -f docker-compose.dev.yml up -d app
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run check
curl -s -o /dev/null -w "%{http_code}\n" http://100.120.243.105:8090/
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
- Delete: `vue.config.js`, `.npmrc`, `babel.config.js`, `.browserslistrc`

- [ ] **Step 1: Remove them**

```bash
git rm vue.config.js .npmrc babel.config.js .browserslistrc
```

`.npmrc` held `node-options=--openssl-legacy-provider`, needed only because webpack 4 hashes with md4. Vite uses esbuild/rollup and never calls it.

- [ ] **Step 2: Verify a clean build from scratch**

```bash
docker compose -f docker-compose.dev.yml down
docker volume rm mx-division-builds_node_modules
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm install
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run build-prod
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm install -D vitest`

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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm test`
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm test`
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm test`
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

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm test`

> Expect some of these to **pass immediately**. That is information, not a
> process failure — it is how the Heolstor `author_id` false premise got
> caught. A test that passes on first run has documented existing behaviour.
> Any that fails has found a real defect: **stop and report it rather than
> editing the test to match the code.**

- [ ] **Step 3: Fix any genuine defect, one at a time**

For each failure, confirm against the formula in `src/utils/Notes.md` before deciding whether the code or the expectation is wrong.

- [ ] **Step 4: Run the full suite**

Run: `docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm test`
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
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm test
docker compose -f docker-compose.dev.yml --profile tools run --rm tools npm run check
```
Expected: both green.

- [ ] **Step 3: Commit**

```bash
git add docs/dev.md
git commit -m "docs: what each check proves, and what neither proves"
```

---

## Done when

- `docker compose -f docker-compose.dev.yml up -d app` serves on `100.120.243.105:8090`, tailnet-only, with hot reload.
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
