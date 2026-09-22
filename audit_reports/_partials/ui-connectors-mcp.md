# Audit slice: UI routers, connectors, hub, MCP, SQL

**Headline: this slice is in better shape than the mock counts suggest.** The two
boundaries most likely to repeat the `{"spec": "flm:npu"}` failure — SQLite and the
OAuth keyring — are exercised for real, not mocked. The genuine exposure is elsewhere:
three places pin a hand-written third-party payload behind a mock, and nothing in the
repo ever re-checks that payload against the real peer.

- SQL: `tests/unit/chat/ui/test_database.py:16` builds a real `ChatDatabase(":memory:")`.
  No SQL is mocked anywhere in this slice.
- Keyring: `tests/unit/connectors/conftest.py` installs a real in-memory keyring backend
  (autouse), so the serialize/deserialize + `client_id_hash` tripwire contract runs for real.
- OAuth flow: `tests/unit/connectors/test_flow.py` drives the real `start_authorization`,
  extracts `code_challenge`/`state`/`redirect_uri` from the returned URL, and completes a
  real loopback callback — respx mocks only the token endpoint. This is the pattern the
  rest of the repo should copy.
- UI routers: `tests/unit/chat/ui/test_server.py` and `tests/unit/test_hub_router.py` use a
  real `TestClient` against a real in-process app + `:memory:` DB. Request/response shape
  changes on those routes **would** be caught.

---

## 1. Findings

### DANGEROUS

| file:line | test | evidence | why it's dangerous | replacement |
|---|---|---|---|---|
| `tests/unit/eval/test_claude_judge.py:245-252` | `TestGetCompletionTemperature::test_temperature_pinned_when_set` | `kwargs = client.client.messages.create.call_args.kwargs` / `assert kwargs["temperature"] == 0.0` | Mocks the Anthropic SDK and asserts `temperature` reaches `messages.create`. The production judge default is `claude-opus-5` (`src/gaia/eval/config.py:16`), whose family **rejects sampling params with a 400** — the client's own docstring says so (`src/gaia/eval/claude.py:38-41`: *"they reject sampling params with a 400, so pinning a value fails the call outright"*). The test also builds the client with `model="claude-sonnet-4-6"` (`:27`), a model the product never uses, so the fixture quietly picks the one model family where the assertion could hold. This is the exact `{"spec": ...}` shape: the mock accepts a request the real server refuses. **Mitigation, stated honestly:** no production caller passes `temperature` — every construction site is `ClaudeClient()` or `ClaudeClient(model=...)` (`cli.py:4044`, `eval/{action_item,briefing,draft}_quality.py`, `pdf_document_generator.py:34`). It is a live trap for the next caller, not a live bug. | Delete the pin, or convert it to an assertion that the param is **rejected** for the default model. If the `temperature=` constructor arg has no caller, the honest fix is to drop the arg. |
| `tests/unit/chat/ui/test_server.py:166-760` (13 tests, e.g. `test_system_status_context_size_insufficient:214`, `test_system_status_model_loaded_derived_from_all_models_loaded:539`) | `TestSystemStatus::*` | `@patch("httpx.AsyncClient")` + hand-written body `{"all_models_loaded": [{"model_name": ..., "recipe_options": {"ctx_size": 4096}}]}`; `assert data["model_context_size"] == 4096` | Pins Lemonade's `/health` and `/models` response shape from memory. Four production files parse that exact path — `ui/routers/system.py:492,507,528,827`, `ui/server.py:324,347`, `ui/_chat_helpers.py:1207,1217,1271,1276`. If Lemonade renames `recipe_options.ctx_size` or drops `all_models_loaded`, all 13 tests stay green while the UI silently reports the wrong context size and fires (or suppresses) the "context too small" banner. Direction is inbound rather than outbound, but the failure mode is identical: a hand-written peer payload nobody re-validates. | Keep the unit tests for the branch logic, and add **one** `require_lemonade` integration test that hits the real `/health` and asserts the keys the parser depends on exist. Model it on `tests/integration/test_lemonade_backend_contract.py`. |
| `tests/unit/test_browser_tools.py:305-336` | `test_parse_ddg_results` | `mock_html = """...<div class="result"><a class="result__a" href="...uddg=...">"""` + `patch.object(self.client, "_request", ...)`; `assert results[0]["url"] == "https://example.com/page"` | Hand-written DuckDuckGo HTML matching the exact selectors in `src/gaia/web/client.py:855-857` (`.result`, `.result__title a, .result__a`, `.result__snippet`). DuckDuckGo changes its scraped HTML without notice; when it does, web search silently returns zero results and this test still passes. It is the only test that exercises the parse at all — every other `search_duckduckgo` test mocks the method wholesale. | Record a real `html.duckduckgo.com` response as a checked-in fixture with a dated header, and add a `@pytest.mark.integration` test that fetches live and asserts ≥1 result — so the fixture's staleness is detectable. |

### MASKING

| file:line | test | evidence | what's hidden | replacement |
|---|---|---|---|---|
| `tests/unit/connectors/test_router_connectors.py:287-298, 316-339, ~648` | `TestConfigureEndpoint::test_configure_dispatches_to_handler`, `TestAuthorizeGrantAgents::*`, `TestAuthorizeDeviceEndpoint::*` | `monkeypatch.setattr("gaia.ui.routers.connectors.configure", AsyncMock(...))`; `monkeypatch.setattr("gaia.connectors.start_authorization", mock_start)` | The route is real (`TestClient`, real registry, real grants dir — good), but the flow never runs. No test drives **"user has never connected"** through the HTTP route: empty keyring → `POST /api/connectors/google/authorize` → real `start_authorization` → real authorization URL. That cold path is covered only at function level in `test_flow.py`. A regression in how the router marshals `scopes`/`grant_agents` into the real flow would pass. | One route-level test with an empty keyring and respx mocking only `oauth2.googleapis.com/token`, asserting the returned `authorization_url` carries `code_challenge_method=S256` and the requested scopes. |
| `tests/unit/test_browser_tools.py:840-845` | `test_search_web_no_results` | `self.agent._web_client.search_duckduckgo.return_value = []` / `assert "No results found" in result` | Tests the tool's *formatting* of an empty list. The real zero-results path — DDG returns HTML with no `.result` divs — is never exercised, so a selector break presents as an empty list here and as a silent failure in production. | Feed the empty case through `search_duckduckgo` with a no-results HTML fixture instead of stubbing the method. |
| `tests/unit/chat/ui/test_indexing_errors.py:36-112` | `TestIndexingErrors::*` | `mock_rag = MagicMock()` + `patch("gaia.rag.sdk.RAGSDK", return_value=mock_rag)` | The empty-index / first-document-ever path is a `MagicMock`, so "index does not exist yet" is whatever the mock returns. Borderline — these tests target error *classification*, which is legitimate unit work. Listed for completeness, not as an action item. | Leave as-is unless RAG cold-start bugs recur. |

### UNNECESSARY

| file:line | test | evidence | why | replacement |
|---|---|---|---|---|
| `tests/unit/test_hub_router.py:29` | app fixture | `app.state.agent_registry = MagicMock(spec=AgentRegistry)` | The registry is a `MagicMock` while everything else in the fixture is real. `spec=` limits the blast radius, and the offline path *is* covered (`test_catalog_offline_503_when_no_cache:81` raises a real `CatalogError`), so this is low priority. | Swap in a small real registry seeded from a temp dir when one becomes cheap to build. |
| `tests/unit/connectors/test_mcp_server.py:91-431` | `McpServerHandler` suite | `patch("gaia.connectors.mcp_server.keyring.set_password")` (×9) | Patches `keyring.set_password` directly even though `tests/unit/connectors/conftest.py` already installs a real in-memory keyring backend for this package. The patch is redundant and weaker than the fixture the file already gets. | Drop the patches; the autouse in-memory backend already isolates them. |

### JUSTIFIED

| file:line | what | why it's fine |
|---|---|---|
| `tests/unit/connectors/conftest.py:48-70` | in-memory keyring backend | A real backend, not a `MagicMock` — the JSON blob contract runs for real. Correct call. |
| `tests/unit/connectors/test_tokens.py` (respx, all) | mocks `oauth2.googleapis.com/token` responses | Live Google OAuth is unmockable in CI (real refresh tokens, rate limits, side effects). `@respx.mock` defaults to `assert_all_called`, so the URL is pinned. Only the **response** is fabricated; that is the right boundary. See the gap below about the request body. |
| `tests/unit/connectors/test_router_forwarded.py:171-203` | captures the refresh body | Actually asserts the outgoing body (`assert FWD_CLIENT_ID in captured["body"]`) — the only place in this slice that does. Worth extending, not replacing. |
| `tests/unit/test_security_edge_cases.py` (e.g. `:61`) | `patch("os.path.realpath")` | OS primitives, not a service contract. Constructing a real symlink-into-a-blocked-dir is platform-specific and flaky on Windows CI. |
| `tests/unit/chat/ui/test_server.py:1194+` | `@patch("gaia.ui.server._get_chat_response")` | Mocks the LLM, not the HTTP layer. Everything around it (routes, DB, response models) is real. |

---

## 2. Coverage gaps

**Per UI router — is the real route exercised?**

| Router | Real `TestClient` + real DB? | Would a response-shape change be caught? |
|---|---|---|
| sessions, documents, files, system, chat (`test_server.py`) | Yes | Yes — field-presence assertions, e.g. `:99-112`, `:1314-1327` |
| hub (`test_hub_router.py`) | Yes (registry is a `MagicMock`) | Yes for status codes; partially for bodies |
| connectors (`test_router_connectors.py`) | Yes, but handlers mocked | Route wiring yes; flow-produced fields no |
| tunnel, goals, memory, mcp, schedules, onboarding, agents | Not in this slice's files | Unknown — flag for the next pass |

**Frontend ↔ backend shape.** No test cross-checks them. TS types in
`src/gaia/apps/webui/src/types/index.ts` are hand-written, not generated, and there is no
OpenAPI snapshot for the UI backend (`grep -rn openapi tests/unit/chat/ui/` → nothing).
The email agent has exactly the right thing — `tests/test_email_openapi_conformance.py`,
which asserts responses conform to a committed schema and that no undocumented fields
leak. Nothing equivalent guards `/api/*`. Reported drift (from a scan, not hand-verified
end to end): backend defaults several fields to non-null (`title_is_custom`, `private`,
`agent_type`, `device` on `SessionResponse`; `detected_devices` on `SystemStatus`) that
the TS side declares optional — harmless today, but exactly the drift a conformance test
exists to catch.

**MCP.** `tests/mcp/` is genuinely mock-free — verified, zero `unittest.mock` hits — but
thin where it matters. No test validates a tool's `inputSchema` against a real client
handshake. `test_email_mcp_stdio_parity.py` spawns a real stdio server and calls
`initialize` + `list_tools` + `call_tool`, but only asserts tool *names*
(`:259-268`). `test_agent_mcp_server_stdio.py:88-100` asserts the schema→signature
translation in-process. The HTTP JSON-RPC tests (`test_mcp_http_validation.py`,
`test_mcp_integration.py`) assume a server is already listening on :8765 and are not in
default CI. Net: a tool whose `inputSchema` drifts from its handler ships uncaught.

**Connectors — the OAuth request body.** The provider body-builders are unit-tested for
real (`test_providers.py:261-268`, `test_microsoft_provider.py:370-376` assert
`grant_type`, `refresh_token`, `client_id`, absence of `client_secret`). What is *not*
pinned anywhere is how `_refresh_token` puts that body on the wire —
`src/gaia/connectors/tokens.py:236` uses `client.post(url, data=body)`, i.e.
form-encoded, which is what Google and Microsoft require. Switch that to `json=body` and
every test in this slice still passes while every real refresh 400s. That is the
`{"spec": ...}` bug with the encoding rather than the field names.
`NEEDS-LIVE-CHECK:` cannot be validated against a live provider in CI; the cheap fix is a
respx `side_effect` that asserts
`request.headers["content-type"] == "application/x-www-form-urlencoded"` and
`parse_qs(request.content)` contains `grant_type=refresh_token`.

---

## 3. Highest-leverage recommendation

**One shared fixture would fix the connectors gap, and it already exists.**
`tests/unit/connectors/test_flow.py` is the template: real flow + real in-memory keyring +
respx on the token endpoint only + a real loopback callback. Lifting that into a shared
fixture and pointing `test_router_connectors.py` at it converts every
`monkeypatch.setattr("gaia.connectors.start_authorization", AsyncMock(...))` in one move,
and gets the cold "never connected" path covered through the HTTP route for free.

Ranked follow-ups:

1. **Assert the outgoing OAuth body at the wire.** One respx `side_effect` in
   `test_tokens.py` closes the highest-similarity gap to the motivating bug. Cheapest fix
   in this report.
2. **One `require_lemonade` health-shape test.** Retires the risk behind 13 mocked
   `TestSystemStatus` tests at once.
3. **A UI-backend OpenAPI conformance test**, copying
   `tests/test_email_openapi_conformance.py`. Only ~31 of ~57 `/api/*` routes declare a
   `response_model`, so this needs the response models filled in first — real work, real
   payoff.
4. **Drop the `temperature` pin** in `test_claude_judge.py`, and drop the constructor arg
   if it stays caller-less.
5. **Extend the MCP stdio parity test** from names to `inputSchema` shape — the server is
   already running in that test; the assertion is a few lines.

## Verification notes

Read directly: `test_tokens.py`, `connectors/tokens.py`, `providers/google.py`,
`test_server.py` (1–1561), `ui/routers/system.py` (parse sites), `eval/claude.py`,
`test_claude_judge.py`, `test_router_connectors.py` (fixtures + configure/authorize),
`test_browser_tools.py` (DDG parse), `web/client.py:836-857`, `test_database.py`,
`test_mcp_server.py`. Router inventory and the frontend/backend field comparison came
from a scan and are labelled as such above. No files were modified; no servers started.
