# Mock-test audit

**Date:** 2026-09-02 · **Scope:** `tests/` (678 test files) and `hub/agents/*/python/tests/`
· **Method:** static read of every heavy-mock file, plus live probes against Lemonade
11.5.0 and a string scan of the shipped server binary. No production code changed.

**Issues filed:** [#3318](https://github.com/amd/gaia/issues/3318) install/uninstall payload ·
[#3319](https://github.com/amd/gaia/issues/3319) `set_params` dead code ·
[#3320](https://github.com/amd/gaia/issues/3320) `LEMONADE_CTX_SIZE` inert ·
[#3321](https://github.com/amd/gaia/issues/3321) email spec omits an endpoint ·
[#3322](https://github.com/amd/gaia/issues/3322) MCP `protocolVersion` ·
[#3323](https://github.com/amd/gaia/issues/3323) live tests that cannot fail ·
[#3324](https://github.com/amd/gaia/issues/3324) Lemonade endpoint coverage ·
[#3325](https://github.com/amd/gaia/issues/3325) cross-process argv contracts ·
[#3326](https://github.com/amd/gaia/issues/3326) OAuth body encoding ·
[#3327](https://github.com/amd/gaia/issues/3327) UI/TS conformance ·
[#3328](https://github.com/amd/gaia/issues/3328) CI lint checks

Per-slice detail (file-by-file, beyond what this summary compresses) is in `_partials/`.

---

## The conclusion

**Four things GAIA sends to an external peer today are rejected or ignored by that peer,
and a mocked test asserts each one is correct.** All four were found by asking one
question the test suite never asks: *would the real thing accept this?*

| What GAIA sends | What actually happens | The test that says it's fine |
|---|---|---|
| `POST /install {"spec":"flm:npu"}` | `400 Both 'recipe' and 'backend' are required` | `test_npu_device_support.py:181` |
| `POST /uninstall {"spec":"flm:npu"}` | `400` — same | `test_npu_device_support.py:221` |
| `POST /params {"temperature":…,"top_k":…}` | `400 Unknown config key` on **every** key | `test_lemonade_client.py:920` |
| `LEMONADE_CTX_SIZE` env var at server launch | string absent from the entire Lemonade 11.5.0 install | 3 tests, in 3 files |

The `install` case is the bug that prompted this audit. The other three were found the
same way and had been sitting green.

Two structural causes, both worth fixing above any individual test:

1. **Payload shape is asserted against a stub.** A mock accepts whatever GAIA sends, so
   pinning the payload only records what we *believe*. 22 sites do this.
2. **Some tests cannot fail.** `test_integration_pull_model` runs against a real server
   and then swallows every error with the comment `# Don't fail the test`.
   `test_integration_health_check_914_format` wraps every response assertion in an `if`,
   so a renamed field prints ⚠️ and passes. These are worse than mocked tests — they read
   as real coverage.

The ratio behind it: **3,935 mock hits in `tests/unit/` against 22 uses of the
`require_lemonade` fixture across the whole repo.**

**The single highest-leverage change** is one live `/health` schema test. Roughly 32
mocked tests hand-build `{"all_models_loaded":[{"recipe_options":{"ctx_size":N}}]}` and
none verify it. That field *already moved once* (Lemonade 9.1.4). One unconditional live
assertion grounds all 32 without rewriting any of them.

**What is *not* broken:** the hub agents, the connectors OAuth flow, the UI routers, and
the daemon/broker tests are in good shape — several already do exactly what this audit
asks and say so in their docstrings. Details in §5.

---

## 1. Counts survey

Files containing `test_*`, counted for `mocker.patch`, `unittest.mock`, `MagicMock`,
`patch(`, `monkeypatch.setattr`, `respx`, `assert_called*_with`, `@patch`.

| directory | test files | files with mocks | total mock hits |
|---|---:|---:|---:|
| `tests/unit/` | 462 | 275 | **3,935** |
| `hub/agents/*/python/tests/` | 149 | 70 | 714 |
| `tests/` (root) | 17 | 10 | 258 |
| `tests/integration/` | 44 | 11 | 169 |
| `tests/installer/` | 2 | 1 | 1 |
| `tests/mcp/` | 9 | 0 | **0** |
| `tests/stress/`, `tests/fixtures/` | 3 | 0 | 0 |

By pattern: `monkeypatch.setattr` 1,580 · `patch(` 1,469 · `MagicMock` 927 · `@patch`
472 · `unittest.mock` imports 231 · `mocker.patch` 163 · `respx` 137 ·
`assert_called_once_with` 93 · `assert_called_with` 5.

**HTTP-layer mocks are concentrated in one file.** Of 63 `responses.add` calls in the
repo, **60 are in `tests/test_lemonade_client.py`** — the client for the largest contract
surface GAIA has is validated almost entirely against a fake server GAIA programs itself.

Heaviest files: `test_init_command.py` (366) · `mcp/client/test_transports.py` (98) ·
`test_lemonade_launcher.py` (88) · `test_lemonade_client.py` (82) ·
`agents/test_builder_agent.py` (77) · `test_rag.py` (76) · `test_llamacpp_backend.py` (72).

**A CI note that matters:** no workflow runs `tests/test_lemonade_client.py` without
`-k "Integration"`. Its ~60 mocked HTTP tests **never execute in CI at all** — they cost
maintenance and return no signal.

---

## 2. Findings

Bucket key — **DANGEROUS**: mocks a contract boundary *and* pins an outgoing
payload/argv/schema shape. **MASKING**: hides the cold/empty starting state, or cannot
fail. **UNNECESSARY**: doubles pure local logic. **JUSTIFIED**: leave alone.

Totals: **21 DANGEROUS · 15 MASKING · 6 UNNECESSARY · 26 JUSTIFIED file-groups.**

### DANGEROUS — verified against the real peer

These four are not suspicions. Each carries a real response captured during this audit.

| # | file:line | test | evidence | replacement |
|---|---|---|---|---|
| **D1** | `tests/unit/test_npu_device_support.py:181` | `test_install_backend` | `assert_called_once_with("post", ".../install", {"spec":"flm:npu"}, timeout=300)`. **Live:** `400 {"error":"Both 'recipe' and 'backend' are required"}`. Split form returns `200 {"recipe":"flm","backend":"npu","status":"success"}`. Broke `gaia init --profile npu`; NPU CI green throughout. | Fix exists on `fix/npu-flm-backend-install` — land it, delete the pin, keep a pure unit test for `_split_backend_spec`. |
| **D2** | `tests/unit/test_npu_device_support.py:221` | `test_uninstall_backend` | `{"spec":"flm:npu"}` to `/uninstall`. **Live:** identical `400`. Never exercised live. | Same contract test. Assert the 400-on-combined-spec rather than actually uninstalling. |
| **D3** | `tests/test_lemonade_client.py:920` | `test_set_params` | `responses.add(POST /params, json={...fabricated echo...}, status=200)`. **Live:** *every* key `set_params()` can send is rejected — `temperature`, `top_p`, `top_k`, `min_length`, `max_length`, `do_sample` each return `400 {"error":"Unknown config key: 'X'"}`. The method is 100% dead. The one integration test that would have caught it is `@pytest.mark.skip("Parameter setting API is still in development")` (`:2470`). **No production caller exists.** | Delete `set_params` and both tests. This is dead code with a fake test proving it works. |
| **D4** | `tests/unit/test_lemonade_launcher.py:205`<br>`tests/unit/installer/test_init_ctx_size.py:211,253`<br>`tests/test_lemonade_client.py:1972,1992` | `test_build_start_command_modern_windows` + 4 siblings | All assert `LEMONADE_CTX_SIZE` is the modern ctx channel. **Verified:** the string appears **nowhere** in the Lemonade 11.5.0 install tree; the binary's env table is `MCP_IMAGE_DIR, API_KEY, ADMIN_API_KEY, DEFAULTS_PATH, CI_MODE, CACHE_DIR, BACKEND_WATCHDOG*, ALLOWED_ORIGINS`. ctx is a **config-file key** (`resources/defaults.json → "ctx_size": -1`). So the #839 fix (auto-started server must come up at profile ctx) is inert — masked because the per-request `/load ctx_size` path works. Two of the three tests also mock `build_start_command` itself, so they assert their own fixture. | One `require_lemonade` test: launch with a distinctive ctx (8192), assert the server reports it. Then switch the channel to `lemonade config set ctx_size=N`. |

### DANGEROUS — verified in-repo (both sides of the contract are here)

| # | file:line | test | evidence | replacement |
|---|---|---|---|---|
| **D5** | `hub/agents/email/python/tests/test_spec_html_artifact.py:26` | `test_committed_spec_html_artifact_is_up_to_date` | **A live shipped-doc defect.** `agent_routes.py:648` registers `POST /autonomy/undo`; `SPEC.md` documents it (2 hits), `SKILL.md` (1 hit), `specification.html` and `spec_html.py` **0 hits** — independently confirmed. The HTML spec served at `GET /v1/email/spec` omits a real endpoint. The test is green because it only compares the generator to its own output. | Enumerate `agent_routes.router.routes` and assert every schema-visible path appears in the rendered spec. Then add the missing section. |
| **D6** | `src/gaia/mcp/client/mcp_client.py:447`, `transports/http.py:50`, `mcp_bridge.py:411` — pinned by `tests/unit/mcp/client/test_mcp_client.py:281` | `test_connect_sends_initialize_request` | GAIA declares `protocolVersion: "1.0.0"`. MCP protocol versions are **date strings** (`2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`); `"1.0.0"` is not one. The transport is a `Mock`, so the test can only ever confirm we send the wrong value. Also verified: GAIA **never** sends `notifications/initialized`, which the spec requires before normal operation. | A toy in-repo stdio MCP server + real `StdioTransport`. Catches the bad version and the missing notification together. No network. |
| **D7** | `hub/agents/email/npm/test/client.test.ts:350,483` | archive/unarchive + calendar path tests | `expect(seen).toEqual([".../confirm", ".../archive", ".../unarchive"])` — the typed client's URLs asserted against a hand-written list under a `vi.fn()` fake fetch, while `openapi.email.json` sits unread in the same repo. A server-side rename regenerates the OpenAPI artifact, leaves the TS client stale, and both suites pass. Only `/query` has a real-sidecar test. | Import `openapi.email.json` in vitest; assert every emitted URL is a key in `spec.paths`. No server needed. |
| **D8** | `hub/agents/gaia/python/tests/test_stdio.py:1142` | `test_the_parser_accepts_the_spellings_the_go_side_pins` | A cross-language argv contract pinned on **one side only**. Go declares the same strings as independent literals (`tui/internal/client/factory.go:43,53`). Neither side reads the other; the test's own docstring predicts the failure (`exited (code 2)` at spawn) then asserts a literal it typed itself. | ~15 lines: grep `factory.go` for every option string `build_parser()` defines. Plus one subprocess smoke test. |
| **D9** | `tests/unit/eval/test_claude_judge.py:245` | `test_temperature_pinned_when_set` | Asserts `temperature=0.0` reaches `messages.create`. The default judge is `claude-opus-5` (`eval/config.py:16`), and `eval/claude.py:38-41` documents that current models **reject sampling params with a 400**. The fixture builds with `model="claude-sonnet-4-6"` — a model the product never uses — quietly selecting the one family where the assertion could hold. Mitigation: no production caller passes `temperature`. A trap for the next caller, not a live bug. | Drop the pin. If the constructor arg stays caller-less, drop the arg. |

### DANGEROUS — payload pins not yet sprung

Correct today only because someone hand-checked them once.

| # | file:line | evidence | replacement |
|---|---|---|---|
| D10 | `tests/unit/test_llamacpp_backend.py:52-132` (6 tests) | Exact-equality pins on the `/load` body (`model_name`, `ctx_size`, `llamacpp_args`, `save_options`, plus `ctx_size=0` asserted as sent and `ctx_size` asserted *absent*) against a patched `_send_request`. **Live check:** `/load` does **not** reject unknown fields — a field could be silently ignored and nothing here would notice. | One live `/load` round-trip sending all four fields, then read ctx back from `/health`. Collapses 6 unit tests into 1 real one. |
| D11 | `tests/test_lemonade_client.py:1032-1037` | `test_pull_embedding_model_request_shape` asserts `user.` prefix + `checkpoint` + `recipe` + `embedding=true` off a `responses` stub. Its docstring claims it proves validity "per CLAUDE.md" — **it does not**; `responses` accepts any body. (The rule *was* independently confirmed live: `/pull` returns `400 "the model name must include the user. prefix"`.) | Promote to `require_lemonade`. Registering a `user.` model is idempotent. |
| D12 | `tests/test_lemonade_client.py:873-885` | `test_unload_model_scoped_vs_global` asserts the body and that *no body means unload-everything* — a pure server-side behaviour claim made entirely against a stub. This is the whole #1544 guarantee. | Live: load two models, scoped-unload one, assert the other is still resident in `/health`. |
| D13 | `tests/unit/chat/ui/test_server.py:166-760` (13 tests) | `@patch("httpx.AsyncClient")` + hand-written `/health` body; `assert data["model_context_size"] == 4096`. Four production files parse that path (`ui/routers/system.py:492,507,528,827`, `ui/server.py:324,347`, `ui/_chat_helpers.py:1207+`). A rename leaves all 13 green while the UI reports the wrong ctx. | Covered by the shared `/health` schema test (P1). |
| D14 | `tests/unit/test_browser_tools.py:305` | `test_parse_ddg_results` — hand-written DuckDuckGo HTML matching the exact selectors in `web/client.py:855-857`. DDG changes scraped HTML without notice; web search then silently returns zero results and this passes. Only test that exercises the parse at all. | Checked-in dated fixture + a `@pytest.mark.integration` live fetch asserting ≥1 result, so staleness is detectable. |
| D15 | `tests/unit/test_npu_device_support.py:174` | Amplifier, not a finding on its own: `LemonadeClient.__new__(LemonadeClient)` bypasses `__init__`, so the tests are also blind to how `base_url` is composed — an `/api/v1` prefix change passes. | Construct normally; it makes no network calls at construction. |

### MASKING — the tests that cannot fail

| file:line | test | evidence |
|---|---|---|
| `tests/test_lemonade_client.py:2559` | `test_integration_pull_model` | **The only live `/pull` test, and it cannot fail.** `except LemonadeClientError as e: print(...)` with the literal comment `# Don't fail the test - pull might fail for various reasons in test environment`. No `self.fail`. It also pulls `TEST_MODEL`, which CI has already cached — the warm-cache trap from #1655, verbatim. |
| `tests/test_lemonade_client.py:2183` | `test_integration_health_check_914_format` | Every meaningful check sits inside `if "all_models_loaded" in health:` / `if "ctx_size" in recipe_options:` with `print("⚠️ …")` on the else. If Lemonade moves `ctx_size` again, this prints a warning and **passes** — while the ~32 mocked tests that invent the shape also pass. |
| `tests/test_lemonade_client.py:2516` | `test_integration_load_model` | Sends the minimal body only. The fields the entire ctx feature rides on are never sent to a real server by any test. |
| `tests/unit/test_chat_preflight.py` (8) · `test_llamacpp_backend.py:171-314` (9) · `test_lemonade_client_ctx_override.py` (15) | ctx-resolution suites | All hand-build the same unverified `/health` shape. **These are correct as unit tests** — they test decision logic, and mocking is right. They just aren't grounded. One live schema test fixes all 32 without touching them. |
| `tests/unit/test_init_command.py:765-855, 2000-2045` | init step tests | `_patch_common_steps` stubs `_install_backend` entirely, so `gaia init --profile npu` has **no** end-to-end coverage. (The decision tests around it, incl. `pull_model.assert_not_called()`, are a real #1655 guard — keep those.) |
| `tests/unit/mcp/client/test_transports.py:68` | `test_send_request_sends_json_rpc` | `stdout` is a `StringIO` with exactly one line that is exactly the matching response. **Verified:** `stdio.py:338` does a bare `readline()` and never correlates the response `id` — the first line back is returned regardless. Real servers interleave notifications. |
| `tests/unit/connectors/test_router_connectors.py:287-339` | configure/authorize routes | Route is real (`TestClient`, real registry, real grants dir) but the flow is `monkeypatch`ed away. Nobody drives *"user has never connected"* through the HTTP route. |
| `tests/unit/test_browser_tools.py:840` | `test_search_web_no_results` | Stubs `search_duckduckgo` to return `[]`. The real zero-results path (HTML with no `.result` divs) is never exercised — so a selector break looks identical to "no matches." |
| `tests/test_lemonade_client.py:2083` · `tests/test_lemonade_embeddings.py` · `tests/test_lemonade_health.py` | gating hygiene | `TestLemonadeClientIntegration` doesn't use `require_lemonade`; with no server up it sets `auto_start=True` and **spawns a real Lemonade on a developer's box**. The other two hard-fail rather than skip. |

### UNNECESSARY

| file:line | evidence |
|---|---|
| `tests/unit/installer/test_init_ctx_size.py:93,134,175,225,277` | Five `@patch(... build_start_command)` on a pure stdlib function, then assertions on its return value — asserting the test's own fixture. Drop the patch; call it for real. |
| `tests/unit/test_lemonade_launcher.py:80,94,114,127,144,294` | `mocker.patch("pathlib.Path.exists", return_value=True)` — a blanket override, so the test cannot distinguish "the canonical path exists" from "every path exists." The macOS tests in the same file already fixed this with `only_these_paths_exist` (`:34`). |
| `tests/unit/test_lemonade_launcher.py:527-728` (7) | `describe_start_hint` tests patch `resolve_lemonade` wholesale, testing only the rendering half. The file's own better test (`:637`) drives the real probe and explains why. |
| `tests/unit/test_npu_device_support.py:228-257` | `get_recipe_status` is a two-line dict lookup; the mock does all the work, over an invented `recipes` shape. (**Live-verified:** the real shape *is* `recipes[r].backends[b].state` — source the fixture from a recorded response.) |
| `tests/unit/connectors/test_mcp_server.py:91-431` | Nine `patch(keyring.set_password)` in a package whose `conftest.py` already installs a real in-memory keyring backend. Redundant and weaker than the fixture it already gets. |
| `tests/unit/test_lemonade_client_ctx_override.py` | `patch.object(LemonadeClient, "load_model")` without `autospec=True` — the mock accepts any signature. Mechanical fix, catches signature drift. |

### JUSTIFIED — leave alone

Named so nobody "fixes" them: transient-retry tests that mock `time.sleep`; corrupt-download
repair routing (destructive to provoke, and it asserts control flow, not wire shape);
`prompt_user_for_delete` patching `isatty`/`input`; `_classify_lemonade_response` and
`MODELS` registry tests (zero doubles, real invariants); auth-header tests (`Bearer` is an
HTTP standard, and the no-leak-on-401 test is a real security guard); `test_webui_build.py`
(a real `npm run build` is multi-minute, and it asserts `shell=False` on every call);
`test_daemon_dev_anchor_spec.py` (isolates from ambient git state, says so);
`test_agent_sidecar_manager.py`'s fake `Popen` (asserts the token appears in *neither* env
nor argv — a security invariant only observable at that seam); `test_transports.py`'s
Popen patches (the assertion target is `_build_argv`'s tokenizer, which is pure);
`connectors/test_tokens.py` respx (live Google OAuth is unmockable in CI; only the
*response* is faked, which is the right boundary); `test_security_edge_cases.py` patching
`os.path.realpath` (OS primitive, not a service contract).

**The house style already exists — cite these, don't change them:** `test_broker_wiring.py`
runs a real broker and a real daemon over HTTP and quotes the CLAUDE.md rule verbatim;
`test_daemon_remedy_commands.py` and `test_sidecar_alive_but_not_serving.py` spawn real
processes; `test_external_mcp_executable.py` drives real PATHEXT resolution;
`connectors/test_flow.py` runs the real OAuth flow with a real in-memory keyring and respx
on the token endpoint only; `hub/agents/email` regenerates its OpenAPI from the real app
and byte-compares; `connectors-demo`'s `_CapturingGet` asserts real outgoing headers.

---

## 3. Coverage gaps — boundaries with no real test at all

### Lemonade HTTP (18 endpoints)

"Real test?" = would it **fail** if the request shape changed, with no double in the path.

| endpoint | method(s) | real test? | risk if it drifts |
|---|---|---|---|
| `POST /install` | `install_backend` | **NO** | **Already realised.** Fix on `fix/npu-flm-backend-install`. |
| `POST /uninstall` | `uninstall_backend` | **NO** | Backend removal silently 400s. |
| `GET /system-info` | `get_system_info`, `get_recipe_status` | **NO** | High — `init._install_backend` reads `recipes[r].backends[b].state` to skip install. A move means re-installing every run, or skipping a missing backend and failing later. |
| `POST /unload` | `unload_model` | **NO** | High — the whole #1544 scoped-unload guarantee is stub-asserted. |
| `POST /pull` (SSE) | `pull_model_stream` | **NO** | High — the first-run download path. Event/field names are invented; a rename is a silent no-progress hang, which users read as a freeze. |
| `POST /pull` (JSON) | `pull_model`, `ensure_model_downloaded` | **effectively NO** | Realised once as #1655. |
| `POST /delete` | `delete_model` | **NO** | Called inside corrupt-model auto-repair; a rejected delete turns a recoverable download into a hard failure. |
| `POST /load` | `load_model` | **PARTIAL** — minimal body only | High for `ctx_size`/`llamacpp_args`/`save_options`. |
| `GET /health` | `health_check`, `get_status`, `validate_context_size` | **request yes, response NO** | **High** — see MASKING. |
| `GET /models?show_all` | `list_models` | **NO** | The `downloaded` flag drives the "already there?" check; a rename means re-pulling every run. |
| `GET /models/{id}` | `get_model_details` | **no test at all** | Low. |
| `POST /params` | `set_params` | integration test is `@skip` | **D3 — dead code.** |
| `POST /completions` | `completions` | **NO** live | Low — legacy. |
| `POST /embeddings` | `embeddings` | **YES** | Best-covered endpoint in the file. |
| `POST /chat/completions` | `chat_completions` | **YES** for the base body | Medium residual: `tools=` and `extra_body` have no live coverage — and tool-calling is core to every agent. |
| `POST /responses`, `GET /stats` | — | **YES** (weak) | Low. |
| `POST /images/generations` | `generate_image` | **PARTIAL** | Low-medium. |

### Other boundaries

- **Subprocess / argv.** No test would catch a contract change for `LemonadeServer.exe`
  (D4), `lemond`, or legacy `lemonade-server serve`. **The cheapest gap by far:** the email
  sidecar argv — the daemon builds `[binary, "--host", H, "--port", P]` (`manager.py:319`)
  and the sidecar's own argparse (`gaia_agent_email/server.py:316`) is in the same repo.
  Nothing cross-checks them; a rename breaks the daemon with a fully green suite. A real
  test is a five-line import.
- **Windows blind spot.** `tests/integration/test_daemon_sidecars.py` is POSIX-only
  (`skipif`, `:46`) *and* drives a toy sidecar. On Windows — GAIA's primary platform — no
  test launches a real supervised sidecar.
- **MCP.** `tests/mcp/` is genuinely mock-free (verified: zero `unittest.mock` hits) but
  thin. No test validates a tool's `inputSchema` against a real handshake — the stdio
  parity test asserts tool *names* only. A tool whose schema drifts from its handler ships
  uncaught. The HTTP JSON-RPC tests assume a server on :8765 and aren't in default CI.
- **Connectors — OAuth wire encoding.** The body *builders* are unit-tested for real. What
  is pinned nowhere is how `tokens.py:236` puts it on the wire: `client.post(url,
  data=body)` — form-encoded, which Google and Microsoft require. **Change `data=` to
  `json=` and every test still passes while every real refresh 400s.** That is the
  `{"spec":…}` bug with the encoding instead of the field names.
- **UI backend ↔ frontend.** No conformance test. TS types in
  `apps/webui/src/types/index.ts` are hand-written; only ~31 of ~57 `/api/*` routes declare
  a `response_model`. The email agent has exactly the right thing
  (`test_email_openapi_conformance.py`); nothing equivalent guards `/api/*`.
- **`hub/agents/gaia`.** Its 523-line npm `SPEC.md` has no drift guard, and no test spawns
  the real stdio agent as a subprocess with the argv the Go TUI passes.

---

## 4. Prioritized top 10

Effort is one engineer, including review.

| # | Action | Fixes | Effort |
|---|---|---|---|
| **P1** | **One live `/health` schema test** (`require_lemonade`, unconditional assertions on `all_models_loaded[0].recipe_options.ctx_size`). Also make `test_integration_health_check_914_format` assert instead of print-and-pass. | Grounds **~32 + 13 = 45** mocked tests without rewriting one. D13, 4 MASKING rows. | **2h** |
| **P2** | **Land the `/install` + `/uninstall` contract test**; delete the payload pins in `test_npu_device_support.py`; keep a pure unit test for `_split_backend_spec`. | D1, D2. Already written. | **1h** |
| **P3** | **Delete `set_params` and its two tests.** Dead code with a fake test proving it works; no production caller. | D3. | **30m** |
| **P4** | **Resolve the `LEMONADE_CTX_SIZE` channel.** Confirm on a clean box, then switch to `lemonade config set ctx_size=N` and delete the assertion in all 3 files. #839 is currently inert. | D4 (×3 files, ×5 tests). | **4h** |
| **P5** | **Add `/autonomy/undo` to the email spec**, and make `test_spec_html_artifact.py` enumerate the real router instead of comparing the generator to itself. | D5 — a live shipped-doc defect. | **2h** |
| **P6** | **Toy in-repo stdio MCP server fixture** (~40 lines, mirroring `tests/fixtures/toy_sidecar.py`); drive real `StdioTransport` + `MCPClient.connect`. Fix `protocolVersion` to a real date string; send `notifications/initialized`; correlate response `id`. | D6, 2 MASKING rows, the MCP coverage gap. No network, no skip. | **1d** |
| **P7** | **Make the live `/pull` and `/load` tests capable of failing.** Remove the `# Don't fail the test` swallow; add a cold `user.`-model registration; send all four `/load` fields and read ctx back. | D10, D11, D12, 3 MASKING rows. Also settles empirically whether the ctx-settle machinery is needed. | **4h** |
| **P8** | **Assert the OAuth body at the wire** — one respx `side_effect` checking `content-type: application/x-www-form-urlencoded` and `parse_qs` contents. Cheapest fix in this report relative to blast radius. | The `data=`/`json=` gap. | **1h** |
| **P9** | **Cross-check the two in-repo argv contracts** — email sidecar (`build_spawn_command` → the sidecar's own parser) and Go↔Python TUI flags (grep `factory.go`). Both sides are in-repo; no service needed. | D8, the sidecar argv gap. | **3h** |
| **P10** | **Mechanical pass:** `require_lemonade`/`integration` markers on the three ungated live files (one currently spawns a real server on dev boxes); drop the 5 `build_start_command` mocks; route the 6 blanket `Path.exists` patches through `only_these_paths_exist`; add `autospec=True`; drop the 9 redundant keyring patches. Pure subtraction. | 6 UNNECESSARY rows + gating hygiene. | **3h** |

**Deliberately not recommended:** rewriting the error-classification matrix, the
corrupt-repair routing tests, or the ctx-resolution decision suites. They assert control
flow, and mocking is the right tool. The only addition worth making there is one live
overflow-provocation test so the `n_ctx` field name is grounded.

---

## 5. Would a lint rule stop the next one? Partly — and the obvious rule is too narrow.

**The narrow rule is cheap and nearly noise-free, but has a low ceiling.** Flagging
`assert_called*_with(...)` that passes a **dict literal** matches exactly **6 sites in 3
files** across the whole repo — and `test_npu_device_support.py` is two of them. Near-zero
false positives, and it would have caught the motivating bug.

But it would have caught **only** that one. D10 uses `payload = mock_send.call_args[0][2]`
then subscripts; D4 asserts an env dict; D3 hides in a `responses.add` fixture. None match.

**The obvious widening is unusable.** Flagging any `.call_args` introspection hits **231
sites in 66 files**; any `assert_called*` hits **372 in 80**. Gating CI on either means 200+
suppressions on day one, and a suppression comment nobody reads is worse than no rule.

**Honest recommendation — ship the narrow rule, but don't expect much of it:**

1. **Do add** the dict-literal rule as a CI check now. 6 sites, ~1h, and it names the
   exact anti-pattern in its error message with a link to the CLAUDE.md section.
2. **Do add** a cheap structural check with real teeth: any test module that patches a
   known wire seam (`_send_request`, `responses.add`, `subprocess.Popen`, `httpx.AsyncClient`)
   **and** asserts an outgoing shape must name a paired contract test in a module-level
   `CONTRACT_TEST = "tests/integration/..."` constant. Mechanical to enforce, and it puts
   the burden where it belongs — on the author who knows whether a real test exists.
3. **Don't** try to lint for MASKING. "This mock hides the cold-start path" is a judgment
   about what the code *does*, and no AST rule reaches it.
4. **The highest-value control is not a linter — it's making the existing live tests able
   to fail.** `# Don't fail the test` and `if "field" in response:` are greppable in about
   ten seconds and account for the two worst findings in this report. A CI check that
   rejects a bare `except` without a re-raise or `self.fail` inside `tests/integration/`
   would be narrow, low-noise, and directly on target.

The durable fix is cultural and already half-landed: the repo has *internalized the
language* of the rule — `test_pull_embedding_model_request_shape`'s docstring cites
CLAUDE.md while asserting against a stub — but applied the wrong remedy. Asserting a shape
harder against a mock is not the fix. Asserting it against the real peer is.
