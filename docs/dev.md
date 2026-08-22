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
```

All three cover different failure modes and none of them substitutes for
another. `npm run check` needs the dev server up; the other two do not.

`build-prod` belongs here rather than under "run anything else": it runs
`vue-tsc` first, and that is the **only** type-check gate in the repo — neither
`npm test` nor `npm run check` type-checks a thing. Skip it before merging and
nothing has.

**None of them proves the app renders.** A green run on all three held while
the app showed nothing but an error screen — and a missing image is quieter
still, because Vite answers a missing asset with its SPA fallback (200
`text/html`) so the browser simply fails to decode it. Open it in a browser.
