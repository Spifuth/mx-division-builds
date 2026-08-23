# TD2 Build API — Reference

This is the HTTP API behind the owner's Division 2 build page. It serves
reference data (weapons, gear, skills, brands, talents, attributes, mods) and
saved builds to a frontend that lives in a sibling repository. If you have
never seen this codebase before, this document is written to be enough on its
own — endpoint list, response shapes, the auth model, and the one gotcha
(`unsupportedFields`) that will otherwise look like a bug.

The code itself is in `api/app/`; `docs/superpowers/specs/2026-08-22-td2-build-api-design.md`
is the original design document this was built from.

## The contract is fixed

**Endpoint paths, query parameters and response shapes are frozen.** The
owner's frontend (a v0-generated Next.js app, in a sibling directory, not this
repo) is already coded against this contract with its own mock data standing
in for the real service. Changing a path or a field name here silently breaks
work that already exists on the other side of the wire. If something in this
contract needs to change, it has to be raised and coordinated, not quietly
adjusted.

## Base URL

The API is tailnet-only. There is no public exposure, no Traefik route, no
DNS record — that is out of scope for this project (see the design spec's
"Out of scope" section). Two addresses reach it, for two different purposes:

| Form | Example | Use it for |
|---|---|---|
| Plain HTTP, tailnet IP | `http://<TAILNET_IP>:8000` | curl, server-to-server calls (the frontend container reaches it at `http://api:8000` over the Docker network), anything that does not need a browser to hold a cookie |
| HTTPS via `tailscale serve` | `https://<host>.ts.net:8444` | anything a browser talks to. The session cookie set by `/api/auth/login` is marked `Secure`, and a plain-HTTP tailnet IP is not a secure context — a browser will accept the response and silently refuse to store the cookie. `tailscale serve` terminates real TLS (a certificate a browser trusts, no `-k` needed) and proxies to `127.0.0.1:8000` inside the host. |

`<TAILNET_IP>` and `<host>.ts.net` are placeholders deliberately: this repo is
public, and no internal address or machine name is committed to it.
`./scripts/dev.sh config` (run on the host) resolves the real tailnet IP; the
tailnet hostname is visible to anyone already on the tailnet.

Confirmed live, both forms, during this task:

```
$ curl -s http://127.0.0.1:8000/api/health
{"ok":true}
$ curl -s https://<host>.ts.net:8444/api/health   # via tailscale serve
{"ok":true}
```

## Authentication

**Every identity today is fake.** The endpoints (`/api/auth/login`,
`/api/auth/callback`, `/api/auth/me`, `/api/auth/logout`) are the real,
final ones — only the identity *provider* behind them is a mock, so the
frontend and its login flow can be built before a Discord application exists.

**How to tell you're talking to the mock, without reading any code:**

1. Every single response — 200s, redirects, 404s, 422s, CORS preflights, all
   of it — carries a `X-Auth-Mode` response header. Confirmed live:
   ```
   $ curl -sD - -o /dev/null http://127.0.0.1:8000/api/health | grep -i x-auth-mode
   x-auth-mode: mock
   ```
2. `GET /api/auth/me` reports the mode in its body (`{"user": ..., "mode": "mock"}`),
   whether or not anyone is logged in.
3. The container logs a `WARNING` at boot naming exactly what is fake:
   ```
   AUTH IS MOCKED - every identity is fake, /api/auth/login trusts a query
   string, and every id it mints is prefixed 'mock:'. Do not expose this.
   ```

**The two switches that control it** (environment variables, `TD2_` prefix):

| Variable | Effect |
|---|---|
| `TD2_AUTH_MODE` | `discord` (default) or `mock`. |
| `TD2_ALLOW_MOCK_AUTH` | Must also be `true`/`1` for mock mode to start. |

Both are required together on purpose: a single typo or a copied `.env` file
setting only `TD2_AUTH_MODE=mock` is not enough to turn real identity off.
`TD2_SESSION_SECRET` is required in every mode — without it the process
refuses to boot at all, because there is nothing to sign the session cookie
with.

**`TD2_AUTH_MODE=discord` without `TD2_DISCORD_CLIENT_ID`, `TD2_DISCORD_CLIENT_SECRET`
and `TD2_DISCORD_REDIRECT_URI` refuses to boot**, and the error names exactly
which of the three is missing. This is deliberate: a service that starts and
then rejects every request is indistinguishable, from the outside, from one
that starts and accepts everyone — so a misconfigured auth layer must stop the
process before it ever binds a socket, not degrade at request time.

**In mock mode**, `GET /api/auth/login?as=<name>` issues a session
immediately — no redirect to a real provider, no callback. The identity it
mints is `{"id": "mock:<name>", "name": "<name>", "mode": "mock"}`; every
mock-minted id is prefixed `mock:`, which no real Discord snowflake can ever
be (a snowflake is decimal digits only), so every mock-owned row stays
findable in one query when real Discord lands. `GET /api/auth/callback`
answers **404** in mock mode (there is nothing to call back to) and **501**
in Discord mode: the Discord token exchange cannot be tested without a real
registered application and real credentials, and untested OAuth code shipped
as if it worked is worse than an honest 501 — so, same reasoning as
`/api/compute`, it is stubbed rather than shipped untested.

The session itself is a signed, `HttpOnly`, `Secure`, `SameSite=Lax` cookie
(`td2_session`), 7-day expiry. `POST /api/auth/logout` clears it.

**CORS**, for a browser-based caller: `http://localhost:3000` and
`https://v0.dev` are allowed explicitly, and `https://*.vercel.app` by regex
(v0 previews deploy there), all with credentials enabled. A page served from
anywhere else cannot read a credentialed response from this API at all — this
is enforced by the browser, not by this API rejecting the request.

## Response shapes

**List endpoints** (everything under "Reference data" below, plus
`GET /api/builds`) answer the same envelope:

```json
{"total": 323, "limit": 100, "offset": 0, "results": [ ... ]}
```

`total` is the number of matches after filtering — not the length of
`results` — so a paginated caller can tell how many pages exist. `limit` and
`offset` echo the request back. Every list endpoint accepts `?limit=&offset=`;
reference-data endpoints also accept `?q=` (case-insensitive substring search
over the fields the response actually shows) — except `/api/tables/{name}`,
the raw escape hatch described below, which takes `?limit=&offset=` only.

**Single-entity endpoints** (`/api/weapons/{name}`, `/api/skills/{id}`)
answer the entity directly, no envelope.

**Errors are not one shape across the whole API**, and this is the one
inconsistency worth knowing about up front:

- Every reference-data endpoint and `/api/compute` uses FastAPI's default —
  `{"detail": "..."}` — on a 404/422/501.
- Every `/api/builds/*` endpoint uses `{"error": "..."}` instead, on purpose:
  the frontend's own error handling reads `body.error`, and a `detail` key
  would have surfaced as "Request failed: 400" with the actual reason
  invisible to whoever hit it.

Confirmed live:

```
$ curl -s http://127.0.0.1:8000/api/weapons/not-a-real-weapon
{"detail":"unknown weapon 'not-a-real-weapon'"}
$ curl -s -X PATCH http://127.0.0.1:8000/api/builds/<id> -d '{"edit_token":"wrong"}'
{"error":"Invalid edit_token"}
```

**The raw escape hatch.** `GET /api/tables/{name}` (one of the 20 CSV table
names — `weapon`, `mask`, `chest`, `backpack`, `gloves`, `holster`,
`kneepads`, `brands`, `brandsetBonuses`, `gearAttributes`, `gearMods`,
`gearTalents`, `skill`, `skillMods`, `skillStats`, `specialization`,
`statsMapping`, `weaponAttributes`, `weaponMods`, `weaponTalents`) returns
the same list envelope, but `results` holds the **unnormalised** rows —
original CSV headers, spaces and all (`"Base Damage"`, not `damage`). It
exists so a column with no home yet in the frontend's types is still
reachable without an API code change.

## Endpoints

### Reference data — read-only, in-memory, no auth

| Method | Path | Notes |
|---|---|---|
| GET | `/api/meta` | dataset version, table list + row counts, snapshot date, upstream source, `unsupportedFields`, last refresh result, degraded-boot flag |
| GET | `/api/weapons` | `?type=&quality=&q=&limit=&offset=` |
| GET | `/api/weapons/{name}` | 404 if unknown |
| GET | `/api/gear` | every slot at once; `?quality=&brand=&q=&limit=&offset=` |
| GET | `/api/gear/{slot}` | `slot` is one of `mask\|chest\|backpack\|gloves\|holster\|kneepads` |
| GET | `/api/brands` | civilian brands joined with their set bonuses |
| GET | `/api/gear-sets` | named gear sets, same bonus-ladder shape as brands |
| GET | `/api/skills` | the 12 base skills, each with its variants |
| GET | `/api/skills/{id}` | addressed by the `id` the list serves, with `tierStats` |
| GET | `/api/specializations` | the 7 specializations |
| GET | `/api/talents/{kind}` | `kind` is `gear\|weapon` |
| GET | `/api/attributes/{kind}` | `kind` is `gear\|weapon` |
| GET | `/api/mods/{kind}` | `kind` is `gear\|weapon\|skill` |
| GET | `/api/tables/{name}` | raw CSV rows, see above |
| GET | `/api/health` | liveness only, independent of the dataset and upstream |

### Saved builds — SQLite-backed, mixed auth

Ownership is enforced two ways at once, deliberately kept independent: an
`edit_token` returned once at creation (the frontend keeps it in
`localStorage`), and the Discord/mock session, when one exists. Either one
that matches is enough; neither is a fallback for the other.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/builds` | `?limit=&offset=`, newest first |
| POST | `/api/builds` | anonymous-writable; 201 `{id, edit_token, url, build}` |
| GET | `/api/builds/{id}` | counts a view, unless the request carries `X-No-View-Count` (used by the frontend's metadata pre-render so one page load doesn't count twice) |
| PATCH | `/api/builds/{id}` | body must include a matching `edit_token`, or the request must carry the owning session; 400 if neither is present, 403 if the token/session is wrong |
| DELETE | `/api/builds/{id}` | same rule as PATCH |

### Auth

| Method | Path | Notes |
|---|---|---|
| GET | `/api/auth/login` | `?as=<name>` in mock mode only; ignored by Discord |
| GET | `/api/auth/callback` | 404 in mock mode, 501 in Discord mode (see above) |
| GET | `/api/auth/me` | `{user: {id, name, mode} \| null, mode: "mock"\|"discord"}` |
| POST | `/api/auth/logout` | 204, clears the cookie |

### Admin

| Method | Path | Notes |
|---|---|---|
| POST | `/api/admin/refresh` | `?force=` to bypass the version check; not authenticated (tailnet-only; would need auth before any public exposure) |

### Compute — Phase 2, not built yet

| Method | Path | Notes |
|---|---|---|
| POST | `/api/compute` | **501**, deliberately — see below |

## The `unsupportedFields` mechanism

The dataset is buildstation.app's CSV export, and it does not carry every
field the frontend's `types.ts` declares. Rather than inventing a value, the
API fills the gap with a typed zero (`0`, `""`, or `[]` — whatever the field's
declared type is) and separately lists every such gap in `GET /api/meta`, so
a caller can grey the field out instead of rendering it as fact.

**This matters concretely: a caller seeing `"accuracy": 0` on a weapon must
not read that as "this weapon has zero accuracy."** It means "this dataset
has no accuracy column at all." Confirmed live, an Exotic assault rifle with
real damage and RPM values and three fields that are exactly this case:

```json
{
  "name": "The Bighorn Full-Auto Mode",
  "quality": "Exotic",
  "damage": 57218,
  "rpm": 800,
  "accuracy": 0,
  "stability": 0,
  "handling": 0
}
```

`GET /api/meta` names every gap, keyed by entity type (a `:category` suffix
where one frontend interface is served by more than one backing table):

```json
"unsupportedFields": {
  "weapon": ["accuracy", "stability", "handling", "brand", "image"],
  "gear": ["armor", "image"],
  "brand": [],
  "gearSet": [],
  "skill": ["description"],
  "specialization": ["signatureWeapon", "description", "perks"],
  "talent:gear": [], "talent:weapon": [],
  "attribute:gear": ["min", "unit", "slot"],
  "attribute:weapon": ["min", "unit", "slot"],
  "mod:gear": ["slot"], "mod:weapon": [], "mod:skill": []
}
```

Six of the thirteen entity types listed — `brand`, `gearSet`, `talent:gear`,
`talent:weapon`, `mod:weapon`, `mod:skill` — have nothing missing at all;
every field their frontend interface declares has a real source column. The
other seven are missing between one and five fields each, twelve distinct
field names in total, and each is missing for its own reason, not a shared
bug:

- **`weapon.accuracy/stability/handling/brand`** — `weapon.csv` simply has no
  such columns.
- **`gear.armor`** — no gear table (mask/chest/backpack/gloves/holster/kneepads)
  carries an armour value.
- **`skill.description`** — `skill.csv`'s `Desc` column is per *variant*, not
  per base skill; there is no row describing the skill as a whole.
- **`specialization.signatureWeapon/description/perks`** — `specialization.csv`
  is three columns, `Name,Stat,Val`, and none of them is any of these three.
- **`attribute:gear/weapon.min/unit`** — both attribute tables carry a `Max`
  and nothing else.
- **`attribute:gear.slot`** — `gearAttributes.csv` has no slot column at all.
- **`attribute:weapon.slot`** — `weaponAttributes.csv` has a `Weapon Type`
  column, but it is empty in all 18 rows today.
- **`mod:gear.slot`** — `gearMods.csv` has no slot column.

**`weapon.image` and `gear.image` are a different kind of gap**, worth calling
out on its own: it is not that the column is missing. `weapon.csv` and all
six gear-slot CSVs carry an `Icon` column with a filename in it — but no image
file exists behind any of them anywhere in this repository. Measured directly
against this checkout: `weapon.csv` is 323 rows, the six gear-slot tables sum
to 487 rows (810 item rows total), and `public/icons/` contains exactly two
subdirectories — `brands/` (64 files) and `skills/` (42 files). There is no
`public/icons/weapons/` or `public/icons/gear/` at all. So every weapon and
every gear piece is missing its icon, not some of them — which is why the API
does not even attempt to serve a broken filename for `image`; it omits the
key, and `types.ts` declares `image` optional for exactly this reason.

## Examples

### A reference endpoint — `GET /api/weapons`

```
$ curl -s "http://127.0.0.1:8000/api/weapons?limit=1"
```
```json
{
  "total": 323,
  "limit": 1,
  "offset": 0,
  "results": [
    {
      "id": "weapon-the-bighorn-full-auto-mode",
      "name": "The Bighorn Full-Auto Mode",
      "type": "Assault Rifle",
      "quality": "Exotic",
      "damage": 57218,
      "rpm": 800,
      "magazine": 42,
      "optimalRange": 40,
      "accuracy": 0,
      "stability": 0,
      "handling": 0,
      "talents": ["Big Game Hunter"]
    }
  ]
}
```

### A build — create, then read

The example below was created live against the running dev API to capture a
real response, then deleted with its own `edit_token` immediately afterward
so it would not sit in the owner's live build list — `kn_xj6y4cOo` will 404
if you look it up; run the command yourself to get a build of your own.

```
$ curl -s -X POST http://127.0.0.1:8000/api/builds -d '{
    "name": "Example Build", "notes": "", "shdLevel": 1500,
    "shdPerks": {"offense": {"weaponDamage": 25}},
    "loadout": {"Primary": "Chatterbox", "Mask": "Ceska Vyroba Mask"}
  }'
```
```json
{
  "id": "kn_xj6y4cOo",
  "edit_token": "L2o8pGjrhI3l0wAtPeudYg",
  "url": "/build/kn_xj6y4cOo",
  "build": {
    "id": "kn_xj6y4cOo",
    "name": "Example Build",
    "notes": "",
    "shdLevel": 1500,
    "shdPerks": { "offense": {"weaponDamage": 25, "headshotDamage": 0, "criticalHitChance": 0, "criticalHitDamage": 0}, "...": "the other three nodes, all zeroed" },
    "loadout": { "Primary": "Chatterbox", "Mask": "Ceska Vyroba Mask", "...": "the other ten slots, null" },
    "views": 0,
    "createdAt": "2026-08-23T12:59:08.802Z",
    "updatedAt": "2026-08-23T12:59:08.802Z"
  }
}
```

`edit_token` is returned exactly once, here, and appears on no read path —
treat it like a credential (it is one; anyone holding it can edit or delete
this build). Save it, then:

```
$ curl -s http://127.0.0.1:8000/api/builds/kn_xj6y4cOo
```

returns the `build` object above without `edit_token`, and `views` is now
`1` — a `GET` on this path counts as a view unless it carries
`X-No-View-Count`.

### The compute stub

```
$ curl -s -X POST http://127.0.0.1:8000/api/compute \
    -H "Content-Type: application/json" -d '{"slots": {}}'
```
```json
{"detail": "Damage computation is Phase 2 and is not implemented yet."}
```
The explicit `Content-Type` header matters here and not on the `/api/builds`
examples above: builds parses the raw request body as JSON regardless of what
header it arrives with, but this endpoint takes a normal FastAPI body
parameter, which needs the header to recognise the body as JSON at all — drop
it and curl's default `application/x-www-form-urlencoded` gets you a `422`
instead of the `501` below.

Status `501`, not `404` — the path is real and wired into the frontend
already; it simply has no implementation behind it yet. When Phase 2 lands
(the damage math, ported from `src/utils/statsService.js`), no frontend
change is needed, only this handler.
