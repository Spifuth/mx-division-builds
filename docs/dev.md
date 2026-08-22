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

mxrun npm run check http://<TAILNET_IP>:8090/   # data sources: reachable + schema-valid
mxrun npm run build-prod # production build into dist/
mxrun npm run lint       # exits non-zero on pre-existing issues -- expected, not a regression
mxrun npm test           # not wired up yet -- lands in a later phase
mxrun npm install <pkg>
```

## Config

Nothing is required. The data tables ship in `public/data/` (refresh them with
`scripts/import-snapshot.mjs <snapshot-dir>`, which also rewrites
`public/DB.Version` — the cache key browsers compare against), and the
dev-server bind address is not stored anywhere: `scripts/dev.sh` derives it
from `tailscale0`.

`.env.local` is gitignored and entirely optional — compose declares it
`required: false`, so a fresh clone builds and starts without one.
