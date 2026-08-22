# Development

Everything runs in Docker. The host needs Docker and nothing else — no Node,
no npm, no yarn.

## Start the dev server

```sh
./scripts/dev.sh up -d app
./scripts/dev.sh logs -f app
```

Serves on `http://<TAILNET_IP>:8090/` — tailnet-only, where `<TAILNET_IP>` is
the tailnet address that `scripts/dev.sh` derives at start-up (it reads the
host's `tailscale0` interface). Run `./scripts/dev.sh config` to see the
actual address it resolved for your machine. Hot reload is on; edit a file
and the browser updates.

## Run anything else

```sh
alias mxrun='./scripts/dev.sh --profile tools run --rm tools'

mxrun npm run lint       # exits non-zero on pre-existing issues -- expected, not a regression
mxrun npm install <pkg>
mxrun npm audit
```

The commands that actually verify something are in **Verifying** below.

## Config

Nothing is required. The data tables ship in `public/data/` (refresh them with
`scripts/import-snapshot.mjs <snapshot-dir>`, which also rewrites
`public/DB.Version` — the cache key browsers compare against), and the
dev-server bind address is not stored anywhere: `scripts/dev.sh` derives it
from `tailscale0`.

`.env.local` is gitignored and entirely optional — compose declares it
`required: false`, so a fresh clone builds and starts without one.

## Verifying

```sh
mxrun npm test                                # unit tests -- the damage math, offline, no network
mxrun npm run check http://<TAILNET_IP>:8090/ # data layer -- reachable, CSV-shaped, schema-valid, and every icon it names ships
mxrun npm run build-prod                      # types + production build into dist/

./scripts/dev.sh --profile tools up -d browser        # headless chromium sidecar
mxrun npm run check:render http://<TAILNET_IP>:8090/  # does it actually render?
```

All four cover different failure modes and none of them substitutes for
another. `npm run check` and `check:render` need the dev server up; the other
two do not.

`build-prod` belongs here rather than under "run anything else": it runs
`vue-tsc` first, and that is the **only** type-check gate in the repo — neither
`npm test` nor `npm run check` type-checks a thing. Skip it before merging and
nothing has.

`check:render` exists because the other three cannot see runtime. It drives a
real browser over the DevTools protocol and fails on any uncaught exception or
on an empty `#app`. It was written after a green run on all three of the others
held while the app rendered **nothing at all** — a CommonJS interop error that
happened only in the dev server and never in a production build, so
`build-prod` was green precisely because it took a different code path.

**Even `check:render` does not prove the app is correct** — only that it mounted
without throwing. It does catch the catch-all data-failure screen, since a
successful mount displaying an error is still a failure. What it cannot catch is
a missing image: Vite answers a missing asset with its SPA fallback (200
`text/html`), so the browser just fails to decode it and renders a broken tile.
`npm run check` reconciles the icons the data names; nothing reconciles the rest.
Open it in a browser.
