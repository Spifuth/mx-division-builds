# TD2 Build API — Design

**Status:** approved 2026-08-22. Phase 1 only; Phase 2 deferred by the owner.

**Goal:** a backend the owner's v0-generated frontend can call, which serves The
Division 2 reference data, keeps that data fresh from upstream, and stores saved
builds behind Discord OAuth.

**The contract below is already in use.** v0 is building against it with its own
mock data. Endpoint paths, query parameters and response shapes are therefore
**fixed**; changing one breaks work that is happening in parallel. Anything that
needs to change must be raised, not quietly adjusted.

---

## Why this exists

The app currently ships a frozen snapshot of 20 CSV tables in `public/data/`,
pinned at `DB.Version` `26.0-mdb`. That removed a runtime dependency on a third
party, and it also means the data goes stale the day the game updates.

The Division 2 has no official API. `buildstation.app` is the only source, and
the owner intends to replace it eventually — either with something better or
with their own. So the backend must:

1. serve data to a frontend that is not on this machine,
2. refresh that data from upstream without a human copying files,
3. survive that upstream disappearing, and
4. let the source be swapped without touching anything else.

## Global constraints

- **Nothing new on the host OS.** Everything runs in Docker, invoked through
  `./scripts/dev.sh`. The host has no Python and must not gain any.
- **Tailnet-only.** Exposed over `tailscale serve` (HTTPS, real certificate, on
  the tailnet only). No Traefik route, no public DNS, no `/srv/nebula` service.
  Production exposure is explicitly deferred.
- **No internal addresses or machine names in tracked files.** This fork is
  public on GitHub.
- **No secrets in the repo.** Discord credentials come from the environment.
- **The API contract is frozen.** See the note above.
- **Python 3.12**, FastAPI, `aiosqlite`. Dependencies pinned.

## Architecture

```
buildstation.app ──fetch (20 CSVs)──┐
mxswat gh-pages ──DB.Version────────┤
                                    ▼
                              refresh job
                                    │
                            validate │ reject → keep serving last good
                                    ▼
                      dated snapshot on disk
                                    │
                                    ▼
                     app.state (hot-swap, no restart)
                                    │
                                    ▼
                        GET /api/*  →  v0 frontend
```

Reference data is loaded and validated at startup and served from memory. 2,682
rows across 20 tables is small enough that no request handler ever touches disk
for it. A malformed CSV fails the boot rather than a request — the pattern
`presentation-app` already uses.

The only handlers that do I/O are the build endpoints, which hit SQLite.

### Layout

```
api/
  app/
    main.py            # lifespan: load + validate, then serve
    config.py          # pydantic-settings; env only
    loader.py          # CSV -> typed rows; startup only
    snapshots.py       # dated snapshot store, promote/rollback
    refresh.py         # the upstream poll + fetch + validate job
    sources/
      base.py          # the interface a data source implements
      buildstation.py  # the only implementation today
    db.py              # SQLite, WAL, schema
    auth.py            # Discord OAuth + the mock provider
    models.py          # Pydantic response models
    routes/
      data.py  builds.py  auth.py  compute.py  admin.py
  tests/
```

### Why the source is an interface

`sources/base.py` defines `fetch_version()` and `fetch_tables()`. `buildstation.py`
is the only implementation. When the owner finds or builds a better source, it is
a sibling module and one config value — not a rewrite. This is a stated
requirement, not speculation.

## Data refresh

**Change detection is cheap.** `DB.Version` lives on mxswat's `gh-pages` branch
(`raw.githubusercontent.com/mxswat/mx-division-builds/gh-pages/DB.Version`), not
on the buildstation API — that endpoint returns `{"message":"Unknown collection"}`.
Two upstreams, and only the small one gets polled. The 20 tables are pulled only
when the token changes, or on an explicit trigger.

**A fetch is never trusted.** A candidate snapshot is validated before it can
replace the live one:

- all 20 tables present,
- each parses as CSV and is not HTML (upstream returning an error page as 200 is
  the exact failure `check-data-sources.mjs` was rewritten to catch),
- required columns present, per the contracts already encoded in
  `scripts/check-data-sources.mjs`,
- row counts within a sane band of the current snapshot — a table collapsing
  from 300 rows to 2 is a bad fetch, not a game update.

If validation fails, the candidate is discarded, the live snapshot is untouched,
and the failure is visible in `/api/meta` and the logs. **The API keeps serving
the last known-good data when upstream is down, broken, or gone.** That is the
central resilience property, given upstream is a third party nobody controls.

**Snapshots are dated and retained** under a configured directory, so rolling
back does not require re-fetching from the source that just broke.

**Promotion is a hot swap.** A validated snapshot replaces `app.state` in place;
no restart, no dropped requests.

**`public/data/` stays** as the seed and offline fallback: a fresh clone with no
network still boots and serves. The refresh job takes over from there.

**On egress:** this deliberately re-introduces outbound requests that Phase 2 of
the containerisation work removed. The character is different — a scheduled
server-side fetch, not a per-visitor browser fetch — and the frontend still never
contacts a third party. Named here so it is a decision rather than a drift.

## API contract

All responses are JSON. List endpoints accept `?limit=&offset=&q=`.

### Reference data — read-only

| Method | Path | Returns |
|---|---|---|
| GET | `/api/meta` | dataset version, table list, row counts, snapshot date, last refresh result |
| GET | `/api/weapons` | weapons; `?type=&quality=&q=` |
| GET | `/api/weapons/{name}` | one weapon |
| GET | `/api/gear/{slot}` | `mask\|chest\|backpack\|gloves\|holster\|kneepads` |
| GET | `/api/brands` | brands joined with their set bonuses |
| GET | `/api/skills` | skills with variants |
| GET | `/api/skills/{id}` | one skill with its tier stats |
| GET | `/api/specializations` | specializations |
| GET | `/api/talents/gear` | gear talents |
| GET | `/api/talents/weapon` | weapon talents |
| GET | `/api/attributes/{gear\|weapon}` | attribute caps by quality/slot |
| GET | `/api/mods/{gear\|weapon\|skill}` | mods |
| GET | `/api/tables/{name}` | escape hatch: any of the 20 raw |
| GET | `/api/health` | liveness; independent of upstream reachability |

### Saved builds

Ownership is the Discord session. There is no anonymous write.

| Method | Path | Behaviour |
|---|---|---|
| POST | `/api/builds` | create; returns `{id, url}`. `id` is `secrets.token_urlsafe(8)` |
| GET | `/api/builds/{id}` | load; increments `views` |
| PATCH | `/api/builds/{id}` | update; owner only |
| DELETE | `/api/builds/{id}` | delete; owner only |
| GET | `/api/builds` | recent builds; `?limit=` |
| GET | `/api/builds/mine` | the session user's builds |

A build is the twelve slots the app already models — `Mask, Backpack, Chest,
Gloves, Holster, Kneepads, Primary, Secondary, SideArm, Specialization, Skill1,
Skill2` — plus SHD levels, a name and notes.

### Auth

| Method | Path | Behaviour |
|---|---|---|
| GET | `/api/auth/login` | begin OAuth (mock: `?as=<name>` issues a session immediately) |
| GET | `/api/auth/callback` | OAuth return |
| GET | `/api/auth/me` | current user, or `null`; always reports `mode` |
| POST | `/api/auth/logout` | clear session |

### Compute — Phase 2

| Method | Path | Behaviour |
|---|---|---|
| POST | `/api/compute` | **501 Not Implemented** until Phase 2 |

Stubbed deliberately so the frontend can wire the call now and have it start
working without a frontend change.

## Auth design

Mirrors `Heolstor/web/auth.py`: `itsdangerous`-signed session cookies, OAuth
state tokens with a 5-minute expiry, and an allowlist of redirect hosts because
Discord matches `redirect_uri` exactly.

**Mock mode exists so the frontend can be built before a Discord app is
registered.** The endpoints are the real ones; only the identity provider is
fake. It is designed so it cannot be left on unnoticed:

- `AUTH_MODE` defaults to `discord`. Mock must be chosen.
- Mock additionally requires `ALLOW_MOCK_AUTH=1`. Two deliberate switches, so a
  copied env file or a single typo cannot enable it.
- `AUTH_MODE=discord` without client credentials **refuses to boot**. Failing
  closed and loudly, the same reasoning as `${DEV_BIND_IP:?}` in
  `docker-compose.dev.yml`, where an empty value would have silently bound every
  interface.
- Every response carries `X-Auth-Mode`, and `/api/auth/me` reports the mode in
  its body. A mock nobody can see is the actual hazard.
- Tests assert each of these, with a demonstrated failing case. This project has
  shipped four checks that could not fail; this is not adding a fifth.

**Two details taken from Heolstor's scar tissue, up front:**

`author_id` is **TEXT**, not INTEGER. Discord snowflakes exceed JavaScript's
safe-integer limit, so Heolstor's web session holds them as strings while its bot
holds ints, and it needed a `_coerce_author_id` shim to stop the two disagreeing.
Storing text from the start removes that bug class rather than managing it.

Mock users get an `author_id` in a reserved form that cannot collide with a real
snowflake, so at cutover every mock-owned build is findable in one query and can
be purged or adopted. Heolstor grew an `adopt_orphan_builds` function because
this problem arrived unannounced.

## Storage

SQLite via `aiosqlite`, raw SQL, following `Heolstor/shared/database.py`: WAL,
`busy_timeout`, an explicit column-list constant so `SELECT` order cannot drift,
and `secrets.token_urlsafe(8)` ids.

Schema is created at startup and versioned with `PRAGMA user_version`. Heolstor's
`try: ALTER TABLE / except OperationalError: pass` idiom is **not** copied — it
cannot distinguish "column already exists" from a genuine error, and a fresh
project has no reason to inherit it.

The database file lives outside the repo tree, in a Docker volume.

## Testing

Pytest, run in the container. What must be covered:

- loader: each of the 20 tables parses to the expected shape; a malformed CSV
  fails the boot rather than serving partial data.
- refresh validation: HTML-as-200, a missing table, a missing column, and a
  collapsed row count are each **rejected**, and the live snapshot survives each.
  Every one of these needs a demonstrated failing case, not an assertion that
  passes vacuously.
- auth: boot fails with `AUTH_MODE=discord` and no credentials; mock requires
  both switches; `X-Auth-Mode` is present.
- builds: ownership is enforced — a second user cannot PATCH or DELETE another's
  build.
- endpoints: shapes match this document, because v0 is built against it.

## Out of scope

- **The damage math.** Phase 2. `/api/compute` returns 501.
- **Public exposure**, Traefik, Infisical, backups, monitoring. Tailnet only.
- **Real Discord credentials.** Mock until the owner registers the app.
- **Replacing the upstream source.** The interface exists; the second
  implementation does not.
- **Rate limiting and abuse controls.** Not needed on a tailnet-only dev service;
  required before any public exposure.

## Done when

- `./scripts/dev.sh up -d api` serves on the tailnet over HTTPS.
- Every endpoint in the contract answers with the documented shape.
- `/api/compute` returns 501.
- A refresh can be triggered, is validated, and a deliberately corrupted fetch is
  rejected without disturbing the live data.
- Login works end to end in mock mode; a build saved by one mock user cannot be
  edited by another.
- Boot fails, loudly, with `AUTH_MODE=discord` and no credentials.
- The host still has no Python.
