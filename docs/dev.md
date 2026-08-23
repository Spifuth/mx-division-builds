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

## API

A FastAPI backend, in `api/`, alongside the Vue app the rest of this document
covers. It serves Division 2 reference data and saved builds to a separate
Next.js frontend that lives in a sibling repository (`../td2-build-page`, not
part of this checkout) — wired into `docker-compose.dev.yml` as the `ui`
service purely so it shares a Docker network with the API it calls. Full
endpoint reference, response shapes and examples: `docs/api.md`.

### Start it

```sh
./scripts/dev.sh up -d api
./scripts/dev.sh logs -f api
```

Serves on `http://<TAILNET_IP>:8000/`, same tailnet-only pattern as the app
above, plus a loopback bind (`127.0.0.1:8000`) so a browser on this host sees
a secure-enough origin to store the session cookie. It is also published over
real HTTPS on the tailnet via `tailscale serve`, confirmed live during this
task:

```
$ docker exec tailscale tailscale serve status
https://<host>.ts.net:3443 (tailnet only)
|-- / proxy http://127.0.0.1:3000
https://<host>.ts.net:8443 (tailnet only)
|-- / proxy http://127.0.0.1:5005
https://<host>.ts.net:8444 (tailnet only)
|-- / proxy http://127.0.0.1:8000
```

`:3443` is the UI, `:8444` is this API. `:8443` is a pre-existing mapping for
an unrelated service on this host (port 5005) — leave it alone; nothing in
this project owns it.

Code edits hot-reload (`uvicorn --reload`, watching `api/` through the bind
mount). After a dependency or environment change, `./scripts/dev.sh restart api`.

### Run its tests

```sh
./scripts/dev.sh --profile tools run --rm api-tests pytest
./scripts/dev.sh --profile tools run --rm api-tests pytest -v   # per-test, verbose
```

256 tests, offline, no network. `api-tests` is a dedicated one-shot service
with its own throwaway snapshot directory and database path, so running the
suite never touches the `api` service's live data.

### Auth is mocked

Every identity today is fake — the endpoints are real, only the identity
*provider* is a stand-in, so the login flow can be built before a Discord
application exists. Two environment variables control it, both required
together on purpose (`docker-compose.dev.yml` already sets both for `api` and
`api-tests`, which is why mock mode is what you get by default in this repo):

```
TD2_AUTH_MODE=mock
TD2_ALLOW_MOCK_AUTH=true
```

`TD2_AUTH_MODE=discord` with real credentials turns it off; with no
credentials the process refuses to boot rather than starting in a half-real
state. Every response carries an `X-Auth-Mode` header, and `GET /api/auth/me`
reports the mode in its body, so which one is live is never a guess — see
`docs/api.md` for the full model.

### `/api/compute` is a deliberate 501

`POST /api/compute` is not built yet — the damage math is Phase 2. It answers
**501**, not 404, on purpose: 404 would be indistinguishable from a typo'd
path, and the frontend already wires this call. Confirmed live:

```sh
$ curl -s -X POST http://127.0.0.1:8000/api/compute -H "Content-Type: application/json" -d '{"slots": {}}'
{"detail":"Damage computation is Phase 2 and is not implemented yet."}
```

### How the UI relates to it

The UI (`../td2-build-page`) is a v0-generated Next.js app with its own mock
API routes; `next.config.mjs` there rewrites the reference-data endpoints and
`/api/builds` to this API (`TD2_API_ORIGIN=http://api:8000` inside the
compose network) and leaves `/api/compute` deliberately unproxied — that path
is still answered by the frontend's own mock until Phase 2 exists. Confirmed
live, both halves, against the running `ui` and `api` containers:

```sh
$ curl -s http://127.0.0.1:3000/api/meta | head -c 60        # proxied to this API
{"datasetVersion":"26.0-mdb","tables":[{"name":"backpack"...
$ curl -s -X POST http://127.0.0.1:3000/api/compute -d '{"loadout": {}}'
{"weapon_damage":0, ... ,"note":"Mock compute — placeholder math pending Phase 2 integration with the real compute service."}
```

Run both services together with `./scripts/dev.sh up -d api ui`.
