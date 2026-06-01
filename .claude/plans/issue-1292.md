---
type: plan
source-issue: 1292
repo: amd/gaia
title: "feat(connectors): forward a pre-authenticated provider connection via API (no re-auth)"
created: 2026-06-01
status: ready
work_type: feature
complexity: medium
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 8
test_command: "python -m pytest tests/unit/connectors/ -x"
build_command: "uv pip install -e \".[dev]\" && uv pip install -e \".[ui]\""
lint_command: "python util/lint.py --all"
branch: feat/issue-1292-forward-connection-api
reflection_iterations: 1
agents_used:
  - general-purpose (code-explorer)
  - general-purpose (code-reviewer, security weight)
---

# Issue #1292 — Forward a pre-authenticated provider connection via API

## Goal
Let a host app that already authenticated a user FORWARD that connection to
GAIA over REST. GAIA persists the forwarded OAuth client (`client_id` +
`client_secret`) and `refresh_token`, then refreshes AS THE HOST APP'S CLIENT.
No second OAuth, no re-auth. Credential FORWARDING/INJECTION (Path A).

## Verified grounding (from code exploration)

### The refresh engine is already client-neutral — the GAP is ingestion
- `gaia/connectors/store.py`
  - `save_provider_credentials(provider, client_id, client_secret)` → keyring slot
    `provider:<provider>` (the *app's* OAuth client).
  - `save_connection(provider, account_email, refresh_token, scopes, client_id_hash)`
    → keyring slot `<provider>:default` (v1 always keys by `DEFAULT_ACCOUNT="default"`;
    `account_email` lives inside the blob for display). **A second save OVERWRITES.**
  - `peek_connection` / `peek_provider_credentials` — side-effect-free reads.
  - `verify_keyring_backend()` — raises `ConnectorsError` on Plaintext/Encrypted/Win32Crypto
    backends. Called at every save/load. THIS is the insecure-keyring loud-failure.
- `gaia/connectors/providers/google.py` — `GoogleOAuthProvider.__init__` resolves
  creds in order: explicit kwargs → keyring (`peek_provider_credentials`) → env.
  Computes `client_id_hash = crc32(client_id)`. So a forwarded client persisted to
  the keyring BEATS the env client.
- `gaia/connectors/providers/__init__.py` — `_registry` dict; lazy `get("google")`.
  Cache eviction = `_registry.pop(provider_id, None)` then next `get()` re-instantiates
  with the forwarded creds (this is the `client_id_hash` recompute).
- `gaia/connectors/tokens.py` — `get_or_refresh` already has a per-`(provider,account)`
  `asyncio.Lock` guarding the refresh path (double-checked locking). **The issue's
  "add a refresh LOCK" is ALREADY satisfied** by `_AccessTokenCache.lock`. Module-level
  `_cache` dict — eviction = `_cache.pop((provider, account), None)`.
  `_refresh_token` posts `provider.refresh_request_body(refresh_token)` to
  `provider.token_url` — body carries `client_id`+`client_secret` from the provider
  instance (= forwarded client after eviction). Google does NOT rotate refresh tokens
  (rotation branch exists but is a no-op for Google) — Microsoft rotation OUT OF SCOPE.
- `gaia/connectors/flow.py::_exchange_code_for_tokens` is the existing ingestion
  template: token-exchange → `save_connection(...)` + emit `connector.oauth.completed`.
  The forwarded import is this MINUS the browser/PKCE dance.
- `gaia/connectors/oauth_pkce.py::configure` "Save & Connect" path is the exact
  prior art for: `save_provider_credentials` + `_provider_registry.pop(provider_id)`.

### Scopes the agent needs (AC3)
`gaia/agents/email/scopes.py`:
- `SCOPE_GMAIL_MODIFY = .../gmail.modify`, `SCOPE_GMAIL_SEND = .../gmail.send`
- `SCOPE_CALENDAR_EVENTS = .../calendar.events`, `SCOPE_CALENDAR_READ = .../calendar.readonly`
- `ALL_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES`
- `AGENT_NAMESPACED_ID = "builtin:email"`
AC3 = "gmail.modify, gmail.send, calendar". Validate forwarded scopes are a SUPERSET
of the required union, else raise loudly. "calendar" satisfied by EITHER calendar.events
OR calendar.readonly (events implies read for our purposes) — require at least
calendar.events (the write scope the agent's calendar tools need).

### Where the REST endpoints live
`src/gaia/ui/routers/connectors.py` (mounted by `gaia/ui/server.py`). This is where
connection lifecycle already lives. The UI server:
- Binds `localhost` by default (`--host localhost`); `TunnelAuthMiddleware` gates every
  non-localhost `/api/*` request behind a Bearer token, with a spoof-resistant localhost
  bypass. THIS is the "localhost-bound / API-key-gated" surface (AC4).
- Mutating routes already require `X-Gaia-UI: 1` (CSRF guard `_require_ui_header`).
The OpenAI-compatible server (`src/gaia/api/`) has NO key gating today — wrong home.

Issue spec path is `/v1/connections/{provider}`. Add a NEW `APIRouter(prefix="/v1/connections")`
in `connectors.py` and include it in `create_app()`. Tunnel auth gates `/api/*` only,
so to keep `/v1/connections` behind the same gate we mount it UNDER the api prefix is
NOT desired (issue says `/v1/connections`). Decision: the UI server's localhost binding
is the primary gate; the new router reuses `_require_ui_header` for mutations. Since
`TunnelAuthMiddleware` only inspects `/api/*`, we ALSO register the `/v1/connections`
prefix in the middleware's gated set so remote/tunnel access requires the token too.
(Add `/v1/` to the middleware gate.)

## Design

### New coordination function (reusable — AC + #1084 sharing)
`gaia/connectors/api.py::import_forwarded_connection(...)`:
```
def import_forwarded_connection(
    *, provider, client_id, client_secret, refresh_token, scopes,
    account_email="", grant_agents=None, required_scopes=None,
) -> dict   # metadata-only summary, NO secret
```
Steps (fail loudly, no fallbacks):
1. `verify_keyring_backend()` — insecure backend → `ConnectorsError` (AC4).
2. Validate required-scope coverage. Default `required_scopes` = email-agent union
   (gmail.modify, gmail.send, calendar.events). Missing → `ScopeMismatchError` (AC3).
3. `save_provider_credentials(provider, client_id, client_secret)` → `provider:<provider>`.
4. `providers._registry.pop(provider, None)`; `prov = get_provider(provider)` →
   recomputes `client_id_hash` from the forwarded client.
5. `save_connection(provider, account_email or "default", refresh_token, scopes,
   client_id_hash=prov.client_id_hash)` → `<provider>:default`.
6. `tokens._cache.pop((provider, account_email or "default"), None)` — evict stale token.
7. For each agent in `grant_agents`: `grant_agent(provider, agent_id, scopes)`.
8. Return `{provider, account_email, scopes, connected_at, grant_agents}` — masked,
   no `refresh_token`/`client_secret`.

### REST endpoints (router `/v1/connections`)
- `POST /v1/connections/{provider}` — body {client_id, client_secret, refresh_token,
  scopes, account_email?, grant_agents?}. Calls `import_forwarded_connection`. CSRF-gated.
  Returns masked summary. 201.
- `GET /v1/connections` and `GET /v1/connections/{provider}` — metadata only, secrets
  MASKED/omitted (reuse `api.list_connections` / `api.get_connection` which already
  omit tokens).
- `DELETE /v1/connections/{provider}` — revoke. Reuse `handler.disconnect` (clears
  connection + provider creds? NO — disconnect clears connection + grants; also clear
  provider creds + evict caches for a full revoke). CSRF-gated. 204.

### Secret hygiene (AC + test AC)
- Request model carries `refresh_token`/`client_secret` (legitimate INPUT).
- `tests/unit/connectors/test_secret_hygiene.py::TestOpenApi` asserts `"refresh_token"`
  absent from the WHOLE `/openapi.json`. A request body legitimately needs it →
  REFINE that test to scan only RESPONSE schemas (request bodies may name the field).
  This is an intentional, called-out test change.
- Responses NEVER include `refresh_token` or `client_secret`.

## Files to change
1. `src/gaia/connectors/api.py` — add `import_forwarded_connection` + export.
2. `src/gaia/connectors/__init__.py` — add to `_API_NAMES`.
3. `src/gaia/ui/routers/connectors.py` — new `/v1/connections` router + models.
4. `src/gaia/ui/server.py` — include new router; extend tunnel-auth gate to `/v1/`.
5. `tests/unit/connectors/test_forwarded_import.py` — NEW unit tests.
6. `tests/unit/connectors/test_router_forwarded.py` — NEW integration tests.
7. `tests/unit/connectors/test_secret_hygiene.py` — refine OpenApi test (response-only).
8. `docs/sdk/infrastructure/connectors.mdx` + `docs/docs.json` — document forwarding,
   incl. host-app UNION-of-scopes requirement (GAIA cannot add scope at refresh time).

## TDD test list
Unit (`test_forwarded_import.py`):
- writes `provider:<provider>` (client) + `<provider>:default` (refresh) slots
- computes `client_id_hash` from FORWARDED client (not env client)
- evicts provider cache AND token cache
- scope-shortfall (missing gmail.send) → `ScopeMismatchError` loudly
- insecure keyring backend → `ConnectorsError` loudly (PlaintextKeyring)
- return value masks/omits refresh_token + client_secret
- grant_agents → grants written
- refresh after import uses forwarded client (stub token endpoint, assert request body
  carries forwarded client_id+secret), token returned, NO interactive step

Integration (`test_router_forwarded.py`, `ui_api_client`):
- POST persists (then GET shows configured, masked) — full lifecycle
- POST without X-Gaia-UI → 403
- POST scope-shortfall → 403 + structured error
- GET/GET{provider} never echo secret
- DELETE revokes → subsequent GET 404/empty
- agent acts on mailbox after forward with NO OAuth flow (assert via get_access_token
  returning the stubbed token under a granted agent context)

## Out of scope (explicit)
- Microsoft refresh-token rotation (#1280/#1105).
- Headless CLI import wiring (#1084) — function is factored reusable, not wired.
- Live Google refresh (no creds) — orchestrator runs synthetic-grant smoke only.
