# Audit slice: the Lemonade HTTP client boundary

**Scope:** `src/gaia/llm/lemonade_client.py` (18 endpoints) and every test that mocks it.
**Method:** read-only. No server started, no test run, no file changed outside this report.

## The headline

Ten of the eighteen Lemonade endpoints the client calls have **no test anywhere that
would fail if the request shape changed** — including `/install`, `/uninstall`,
`/delete`, `/unload`, `/system-info` and the SSE `/pull` stream. `/install` is the one
that already bit us; the other nine are the same trap, not yet sprung.

Two structural patterns cause it, and both are worth fixing above any individual test:

1. **Payload shape is asserted against a stub.** `test_npu_device_support.py` pins
   `{"spec": "flm:npu"}` — a body real Lemonade 400s. Ten more assertions across
   `test_llamacpp_backend.py` and `test_lemonade_client.py` pin `/load`, `/pull` and
   `/unload` bodies the same way. They are correct today only because someone
   hand-checked them once.
2. **The `/health` response shape is invented in ~25 tests and verified in none.** Every
   ctx-pinning test builds `LemonadeStatus(loaded_models=[{"id":…, "recipe_options":
   {"ctx_size":…}}])` by hand. That field *already moved once* (Lemonade 9.1.4 relocated
   it from top-level `context_size`), and the one live test that looks at it wraps every
   assertion in an `if`, so it prints a warning and passes when the field vanishes.

The fix for both is one shared fixture, not 35 test edits — see §3.

---

## 1. Findings table

Bucket key: **DANGEROUS** = mocks a contract boundary *and* asserts an outgoing
payload/argv shape. **MASKING** = hides the cold/empty starting state. **UNNECESSARY** =
doubles pure local logic. **JUSTIFIED** = leave it alone.

### DANGEROUS

| file:line | test | risk | evidence | proposed replacement |
|---|---|---|---|---|
| `tests/unit/test_npu_device_support.py:181` | `TestLemonadeClientBackendMethods::test_install_backend` | **Critical — this is the shipped bug.** | `client._send_request.assert_called_once_with("post", ".../install", {"spec": "flm:npu"}, timeout=300)` — real Lemonade replies `400 {"error":"Both 'recipe' and 'backend' are required"}`. Broke `gaia init --profile npu` while NPU CI stayed green. | Delete the payload pin; replace with `tests/integration/test_lemonade_backend_contract.py` (already written on `fix/npu-flm-backend-install`). Keep a unit test only for `_split_backend_spec` (pure function, no doubles needed). |
| `tests/unit/test_npu_device_support.py:198` | `test_install_backend_with_force` | Critical (same contract) | `assert call_args[0][2] == {"spec": "llamacpp:rocm", "force": True}` — pins the wrong body *and* asserts `force` sits alongside it. `NEEDS-LIVE-CHECK:` does the server accept `force` as a sibling of `recipe`/`backend`? `curl -sS -X POST localhost:13305/api/v1/install -H 'Content-Type: application/json' -d '{"recipe":"llamacpp","backend":"rocm","force":true}'` | Same as above — assert `force` survives the split in the contract test. |
| `tests/unit/test_npu_device_support.py:221` | `test_uninstall_backend` | Critical | `assert_called_once_with("post", ".../uninstall", {"spec": "flm:npu"}, timeout=120)`. `/uninstall` takes the same split fields; nothing has ever exercised it live. | Contract test. Uninstall is destructive, so drive it as *assert-the-400-on-combined-spec* (as the fix branch does) rather than actually uninstalling. |
| `tests/unit/test_npu_device_support.py:174` | (all four above) | Amplifier | `client = LemonadeClient.__new__(LemonadeClient)` then hand-sets `base_url`/`log`. Bypassing `__init__` means the tests are also blind to how `base_url` is really composed — a `/api/v1` prefix change passes. | Construct the client normally (`LemonadeClient(host=…, port=…)`); it makes no network calls at construction. |
| `tests/unit/test_llamacpp_backend.py:127` | `TestLoadModelRequestConstruction::test_full_payload_with_all_params` | High | `assert payload == {"model_name":…, "llamacpp_args":…, "ctx_size":2048, "save_options":True}` — an exact-equality pin on the `/load` body, asserted against `@patch.object(LemonadeClient, "_send_request")`. Identical failure mode to `install_backend`; only luck says these four field names are right. | Keep as a *plumbing* test but drop the `==` to a subset check, and add one live `/load` round-trip with all four fields (see §3 gap table row `/load`). |
| `tests/unit/test_llamacpp_backend.py:69, 80, 91, 108` | `test_llamacpp_args_included_in_payload`, `test_ctx_size_included_in_payload`, `test_ctx_size_zero_is_included`, `test_save_options_included_when_true` | High | Each does `payload = mock_send.call_args[0][2]` then pins a field name. `ctx_size=0` in particular is asserted to be *sent* — nothing proves the server accepts 0 rather than 400-ing on it. `NEEDS-LIVE-CHECK:` `curl -sS -X POST localhost:13305/api/v1/load -H 'Content-Type: application/json' -d '{"model_name":"Qwen3-0.6B-GGUF","ctx_size":0}'` | Fold into the one live `/load` test; these five unit tests collapse to a single parametrized live case. |
| `tests/unit/test_llamacpp_backend.py:52-56` | `test_basic_load_sends_model_name_only` | Medium | Asserts *absence*: `assert "ctx_size" not in payload`. Negative shape pins are the same class of claim — they encode a belief about what the server tolerates. | Same live test; assert the minimal body is accepted. |
| `tests/test_lemonade_client.py:1032-1037` | `test_pull_embedding_model_request_shape` | Medium-high | Asserts the `/pull` body carries `user.` prefix + `checkpoint` + `recipe=llamacpp` + `embedding=True`, read off a `responses`-stubbed endpoint. The docstring explicitly claims it proves validity ("mocks prove validity, not just invocation") — **it does not**; `responses` accepts any body. Lower likelihood than `/install` because the shape came out of a real #1745 incident, but the enforcement is fictional. | Promote to `require_lemonade`. Registering a `user.`-namespaced model is idempotent on a live server, so the round-trip is safe to run for real. |
| `tests/test_lemonade_client.py:873-876, 885` | `test_unload_model_scoped_vs_global` | Medium-high | `assert json.loads(responses.calls[-1].request.body) == {"model_name": embed_model}` and `assertIsNone(...request.body)` for the global form. The *no-body-means-unload-everything* claim is a pure server-side behaviour assertion made entirely against a stub. | `require_lemonade` integration test: load two models, scoped-unload one, assert via `/health` that the other is still resident. That is the actual #1544 guarantee. |
| `tests/unit/installer/test_init_ctx_size.py:104-128, 161, 202-212, 248-253` | `test_linux_legacy_auto_start_includes_ctx_size` and siblings | Medium — **circular** | The test sets `mock_build_cmd.return_value = StartSpec(argv=[…,"--ctx-size","32768"])`, then asserts `"--ctx-size" in argv`. It is asserting the stub's own return value survived the trip to `Popen`. It proves argv plumbing and env-merge (genuinely useful — `GAIA_TEST_SENTINEL`/`PATH` retention is a real bug class), but proves nothing about whether the installed `lemonade-server` accepts `--ctx-size`. | Keep the plumbing/env-merge assertions. Move flag *validity* into a test that calls the real `build_start_command` against a resolved tooling record, plus one CLI smoke test that actually launches and reads back ctx from `/health`. The parametrized profile test at :290-306 is better (its `side_effect` echoes `ctx_size`) — model the others on it. |
| `tests/unit/test_llamacpp_backend.py:363-365, 386` | `TestLaunchServerCtxSize` | Medium | Same circularity for the legacy path: `cmd = mock_popen.call_args[0][0]; assert "--ctx-size" in cmd`. Honest docstring admits it is pinned to legacy tooling that modern Lemonade no longer uses. | Consider deleting — it pins a path the docstring says is superseded. If kept, mark it explicitly as a legacy-compat regression guard. |

### MASKING

| file:line | test | risk | evidence | proposed replacement |
|---|---|---|---|---|
| `tests/test_lemonade_client.py:2559` | `test_integration_pull_model` | **Critical — this is the only live `/pull` test and it cannot fail.** | Runs against a real server, then: `except LemonadeClientError as e: … print(f"❌ Unexpected error during model pull: {error_str}")` with the comment `# Don't fail the test - pull might fail for various reasons in test environment`. No `self.fail`. It also pulls `TEST_MODEL`, which CI has already downloaded — the warm cache from #1655, exactly. | Make it fail. Split into (a) already-present pull → assert `status in {success, ok}` and *raise* otherwise; (b) a cold registration of a tiny `user.`-namespaced model to exercise the checkpoint+recipe branch. |
| `tests/test_lemonade_client.py:2183-2295` | `test_integration_health_check_914_format` | **Critical — conditional assertions.** | Every meaningful check is inside `if "all_models_loaded" in health:` / `if "ctx_size" in recipe_options:`, with `print("⚠️ …")` on the else. If Lemonade moves `ctx_size` again the test prints a warning and **passes** — while ~25 mocked tests that hand-build that shape also pass. The whole ctx-pinning feature would silently break. | Assert unconditionally: `all_models_loaded` present, first entry has `recipe_options.ctx_size` as a positive int. If the legacy fallback still needs support, make it two tests gated on the server version, not one test gated on the response. |
| `tests/test_lemonade_client.py:2516` | `test_integration_load_model` | High | Loads `TEST_MODEL` with the minimal body only. The fields the ctx feature depends on (`ctx_size`, `llamacpp_args`, `save_options`) are never sent to a real server by any test. | Extend to send all four and read the resulting ctx back from `/health`. One test closes both this row and five `test_llamacpp_backend.py` rows. |
| `tests/unit/test_chat_preflight.py:31-52` and all 8 tests | `_health_ok()` / `_model()` helpers | High | The preflight's entire input is a hand-authored `{"all_models_loaded": [{"type":…, "model_name":…, "recipe_options": {"ctx_size":…}}]}`. `httpx.get` is patched. A field rename passes every test here *and* in `test_llamacpp_backend.py` *and* in `test_lemonade_client_ctx_override.py` simultaneously. | Don't rewrite these — they're testing decision logic and mocking is right. Instead add **one** live schema test (§3) that asserts real `/health` matches the shape these helpers assume. That converts 8 fictional tests into 8 grounded ones. |
| `tests/unit/test_llamacpp_backend.py:171-314` (9 tests) | `TestEnsureModelLoadedCtxResolution` | High (same root cause) | `LemonadeStatus(loaded_models=[{"id":…, "recipe_options": {"ctx_size": 4096}}])` invented in every case. | Same shared live schema test. |
| `tests/unit/test_lemonade_client_ctx_override.py:64-88` + all 15 tests | `_status()/_present()/_absent()` | High (same root cause) | Same invented shape, plus an invented *async settling* model (`/load` no-ops with success while leaving stale ctx). That behavioural claim is the entire justification for the unload→poll→load→poll sequence and is asserted only against `side_effect` lists. | Same shared live schema test for the shape. For the settling behaviour: `NEEDS-LIVE-CHECK:` load at 65536, then `POST /load` again with `ctx_size=16384` and read `/health` — `curl -sS -X POST localhost:13305/api/v1/load -d '{"model_name":"Gemma-4-E4B-it-GGUF","ctx_size":16384}' -H 'Content-Type: application/json' && curl -sS localhost:13305/api/v1/health \| python -m json.tool`. If the ctx *does* change, the whole settle machinery is over-engineering. |
| `tests/unit/test_init_command.py:765-855, 2000-2045` | `_download_models` / `_install_backend` step tests | Medium | `mock_client.ensure_model_downloaded.return_value = True` — asserts *which model names* are requested (a genuinely useful decision test, and `mock_client.pull_model.assert_not_called()` is a real #1655 guard), but never touches a download. `_patch_common_steps` stubs `_install_backend` entirely, so `gaia init --profile npu` has no end-to-end coverage at all. | Keep the decision tests. Add one CLI-level test: `gaia init --profile npu --force-models` on an NPU runner, asserting the backend reaches `state == "installed"` in `/system-info`. |
| `tests/unit/test_init_command.py:1265-1295` | `test_skip_install_when_probe_succeeds`, `…env_var_set…` | Medium | `mock_client._send_request.return_value = {"status": "ok"}` — the whole client is a `MagicMock`, so a probe pointed at a wrong URL or method still "succeeds". Tests the branch, not the probe. | Narrow the patch to `requests.get` (or use `responses`) so the probe's real URL/method is exercised. |
| `tests/unit/test_lemonade_manager_preload.py:55-160` | `test_idle_server_triggers_preload_with_ctx_size` etc. | Medium | Fully stubbed `LemonadeClient`; `auto_download=True` is asserted as a kwarg. The docstring says the point is "first-run users (no model on disk) get a download instead of a silent failure" — but no test starts from no-model-on-disk. | Keep the kwarg assertion. Cover the actual first-run claim in the `gaia init` cold-start CLI test above. |
| `tests/unit/test_lemonade_error_classification.py` (whole file) | `_classify_lemonade_response` cases | Medium | Every error envelope (`{"error": {"type": "exceed_context_size", "n_ctx": 4096}}`, `details.response.error` nesting) is hand-authored. Nothing captures a real Lemonade error body. If the server renames `n_ctx`, retry classification silently degrades to "not retryable" and long-context chats stop self-healing. | Add one live test that *provokes* an overflow (send a prompt past the loaded ctx) and asserts the raw envelope contains `type` and `n_ctx`. Then the mocked matrix is grounded. |
| `tests/test_lemonade_client.py:2083-2170` | `TestLemonadeClientIntegration.setUpClass` | Low-medium (hygiene) | Does not use `require_lemonade`. If no server is up it constructs the client with `auto_start=True`, i.e. a "unit" file can spawn a real Lemonade on a developer's box. | Gate the class on `require_lemonade` (or an equivalent `setUpClass` skip) instead of auto-starting. |
| `tests/test_lemonade_embeddings.py`, `tests/test_lemonade_health.py` | whole files | Low (hygiene) | Live tests with no `pytest.mark.integration` and no `require_lemonade`; they hard-fail rather than skip off a server. They *are* the only real `/embeddings` coverage, so they're valuable — just ungated. | Add `pytestmark = pytest.mark.integration` and the `require_lemonade` fixture. |

### UNNECESSARY

| file:line | test | evidence | replacement |
|---|---|---|---|
| `tests/unit/test_npu_device_support.py:228-257` | `test_get_recipe_status_found/_not_found` | `client.get_system_info = MagicMock(return_value={"recipes": {"flm": {"backends": {"npu": {"state":"installed"}}}}})`. The method under test is a two-line dict lookup; the mock is doing all the work, and the `recipes` shape it asserts is unverified (see gap table `/system-info`). | Keep the dict-lookup unit test, but source the fixture payload from a recorded live `/system-info` response rather than inventing it. |
| `tests/unit/test_lemonade_client_ctx_override.py:162, 209, 241` etc. | `mock_load.assert_called_once_with(model, auto_download=True, prompt=False, ctx_size=…)` | These pin an *internal* Python seam, not a wire contract, so they're not DANGEROUS. But `patch.object(LemonadeClient, "load_model")` without `autospec=True` means the mock accepts any signature. | Add `autospec=True` to these patches. Cheap, mechanical, catches signature drift. |

### JUSTIFIED

| file:line | test | why it's fine |
|---|---|---|
| `tests/unit/test_llamacpp_backend.py:398-472` | `TestParamSeparation` | Mocks the OpenAI SDK client to assert `repeat_penalty` lands in `extra_body` and `frequency_penalty` stays top-level. This is a *client-side routing* decision; the SDK's typed surface is the contract and it's stable. `NEEDS-LIVE-CHECK:` only whether Lemonade honours `extra_body.repeat_penalty` at all — `curl -sS -X POST localhost:13305/api/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"Gemma-4-E4B-it-GGUF","messages":[{"role":"user","content":"hi"}],"repeat_penalty":1.2}'`. |
| `tests/unit/test_lemonade_model_loading.py:361-397` | `TestPromptUserForDeleteNonInteractive` | Patches `sys.stdin.isatty` / `builtins.input`. Interactive TTY state is exactly what you must fake. |
| `tests/unit/test_lemonade_model_loading.py:403-565`, `tests/unit/test_lemonade_error_classification.py:473-570` | corrupt-download repair routing | Mocks `_send_request` to *raise*, and mocks `pull_model_stream`. These assert control flow (exactly one delete, exactly two pulls, no prompts, actionable message) — no outgoing payload is pinned, and provoking a genuinely corrupt model download is destructive and slow. Correct use of doubles. |
| `tests/test_lemonade_client.py:707-853` | transient-retry tests | Mock `time.sleep` and force a transient error. Retry/backoff timing is exactly what should be faked. |
| `tests/test_lemonade_client.py:1605-1760` | auth-header tests | Use `responses` and assert on the *outgoing header* the client controls. `Bearer <key>` is a fixed HTTP standard, not a Lemonade-specific contract. Also: `test_401_response_does_not_leak_authorization_header_in_error_message` is a genuinely good security regression guard. |
| `tests/unit/test_llamacpp_backend.py:478-717` | `_classify_lemonade_response` + `TestModelsRegistry` | The registry tests use zero doubles and assert real invariants (`min_ctx_size > 0`, embedding models can't claim tool_calling). Model for the rest of the file. |
| `tests/unit/test_npu_device_support.py:14-105, 263-281, 392-429` | `DeviceConfig`, `INIT_PROFILES`, `profile_ctx_size` | No doubles, real constants, real functions. Nothing to fix. |

---

## 2. Coverage gaps — every endpoint the client calls

"Real test?" = would a test **fail** if the outgoing request shape changed, without any
double in the path. `responses`-stubbed and `_send_request`-patched tests answer **no**.

| endpoint | client method(s) | real test? | risk if shape drifts |
|---|---|---|---|
| `POST /install` | `install_backend` | **NO** (mocked at `test_npu_device_support.py:181`) | **Already realised.** `gaia init --profile npu` fails at setup; NPU CI green throughout. Fix branch adds `tests/integration/test_lemonade_backend_contract.py`. |
| `POST /uninstall` | `uninstall_backend` | **NO** | Backend removal silently 400s. Reachable via `gaia lemonade backends` CLI. |
| `GET /system-info[?verbose=]` | `get_system_info`, `get_recipe_status` | **NO** — mocked only | High. `init._install_backend` reads `recipes[r].backends[b].state == "installed"` to skip install. If `recipes` moves, init either re-installs every run or skips a missing backend and fails later at load. Also drives the NPU device-availability gate. |
| `POST /delete` | `delete_model` | **NO** — `test_delete_model` uses `responses` | Medium-high. `delete_model` is called *inside the corrupt-model auto-repair path*: a rejected delete turns a recoverable corrupt download into a hard failure. |
| `POST /unload` | `unload_model` (scoped + global) | **NO** — `responses` only | High. The whole #1544 guarantee (scoped unload doesn't evict the co-resident chat model) is asserted against a stub. Also load-bearing in the ctx re-pin sequence. |
| `POST /pull` (SSE) | `pull_model_stream` | **NO** — hand-written SSE bodies | High. This is the first-run download path. Event names (`progress`/`complete`/`error`) and field names (`percent`, `bytes_downloaded`) are invented; a rename gives a silent no-progress hang, which users read as a freeze. |
| `POST /pull` (JSON) | `pull_model`, `ensure_model_downloaded` | **effectively NO** — `test_integration_pull_model` swallows every error | Realised once as #1655. `checkpoint`/`recipe`/`embedding`/`mmproj`/`reasoning` field names have zero live enforcement. |
| `POST /load` | `load_model`, `_ensure_model_loaded` | **PARTIAL** — `test_integration_load_model` sends `{model_name}` only | High for the optional fields. `ctx_size` / `llamacpp_args` / `save_options` are never sent live, and the entire ctx-pinning feature rides on them. |
| `POST /params` | `set_params` | **NO** — the integration test is `@pytest.mark.skip("still in development")` | Low. No production caller found. |
| `GET /models/{id}` | `get_model_details` | **NO test at all**, mocked or live | Low-medium. Dead-ish code today; if a caller appears it is untested. |
| `GET /models?show_all=true` | `list_models(show_all=True)` | **NO** — live test calls the bare form | Medium. The `downloaded` boolean drives `ensure_model_downloaded`'s "is it already there" check; a query-param rename means every run re-pulls. |
| `POST /embeddings` | `embeddings` | **YES** — `tests/test_lemonade_embeddings.py`, run live by `test_embeddings.yml` | Low. Best-covered endpoint in the file. Should be marked `integration`. |
| `POST /chat/completions` | `chat_completions` (non-stream + OpenAI stream) | **YES** for the base body — `test_integration_chat_completion`, `…_streaming`, `…hybrid_npu_validation` | Medium residual: `tools=` payload and `extra_body` llama.cpp params have no live coverage, and tool-calling is core to every agent. |
| `POST /completions` | `completions` | **NO** live test (`test_completions` uses `responses`) | Low. Legacy endpoint; agents use `/chat/completions`. |
| `POST /responses` | `responses` | **YES** — `test_integration_responses_endpoint` | Low. |
| `GET /health` | `health_check`, `get_status`, `ready`, `validate_context_size` | **request YES, response NO** | **High** — `test_integration_health_check_914_format` asserts the response shape only inside `if` blocks, so a `recipe_options.ctx_size` move passes silently while invalidating ~25 mocked tests. |
| `GET /stats` | `get_stats` | **YES** (weak) — `test_integration_get_stats` | Low. |
| `POST /images/generations` | `generate_image`, `list_sd_models` | **PARTIAL** — `tests/integration/test_sd_integration.py` runs live but skips when the server is absent | Low-medium. |

---

## 3. Remediation — one fixture converts most of this

Everything above collapses into **four** pieces of work. Ordered by value.

**A. `/install` + `/uninstall` contract test — integration, `require_lemonade`.**
Already written on `fix/npu-flm-backend-install`. Land it, delete the three payload pins
in `test_npu_device_support.py`, keep a pure unit test for `_split_backend_spec`. This is
the fix for the shipped bug.

**B. One live `/health` schema test — the single highest-leverage change.**
`tests/integration/test_lemonade_health_schema.py`, gated on `require_lemonade`, with
**unconditional** assertions:

- `all_models_loaded` is present and a list
- when non-empty, entry 0 has `model_name`, `recipe`, `device`, and
  `recipe_options.ctx_size` as a positive int

That one test grounds roughly **32 mocked tests** that currently invent this shape:
8 in `test_chat_preflight.py`, 9 in `test_llamacpp_backend.py::TestEnsureModelLoadedCtxResolution`,
15 in `test_lemonade_client_ctx_override.py`. None of those need rewriting — they stay
mocked, which is right for decision logic. They just stop being fiction. Pair it with
fixing `test_integration_health_check_914_format` to assert instead of print-and-pass.

**C. Make the live `/pull` and `/load` tests capable of failing.**
- `test_integration_pull_model`: remove the `# Don't fail the test` swallow; `self.fail`
  on an unexpected error. Add a cold-registration case for a small `user.`-namespaced
  model so the `checkpoint`+`recipe`+`embedding` branch is exercised for real — that is
  the #1655 shape and the `test_pull_embedding_model_request_shape` claim.
- `test_integration_load_model`: send `ctx_size` + `llamacpp_args` + `save_options`, then
  read the ctx back from `/health`. Closes five DANGEROUS rows in
  `test_llamacpp_backend.py` at once, and empirically settles whether the ctx-override
  settle machinery is needed.

**D. Cheap mechanical passes.**
- Add `require_lemonade` / `pytest.mark.integration` to `test_lemonade_embeddings.py`,
  `test_lemonade_health.py`, and `TestLemonadeClientIntegration` (which currently
  auto-starts a real server on a dev box).
- Add `autospec=True` to `patch.object(LemonadeClient, "load_model" | "get_status")`
  across `test_lemonade_client_ctx_override.py` and `test_lemonade_model_loading.py`.
- Construct `LemonadeClient` normally in `test_npu_device_support.py` instead of
  `__new__` + hand-set attributes.

**Not worth doing:** rewriting the error-classification matrix or the corrupt-repair
routing tests. They assert control flow, not wire shape, and mocking is the right tool
there. The one addition worth making is a single live overflow-provocation test so the
`n_ctx` field name in that matrix is grounded.

---

## Live checks I did not run

Marked `NEEDS-LIVE-CHECK:` inline. Consolidated:

```bash
# 1. Does /install accept `force` alongside the split recipe/backend?
curl -sS -X POST localhost:13305/api/v1/install -H 'Content-Type: application/json' \
  -d '{"recipe":"llamacpp","backend":"rocm","force":true}'

# 2. Is ctx_size=0 accepted on /load, or a 400?
curl -sS -X POST localhost:13305/api/v1/load -H 'Content-Type: application/json' \
  -d '{"model_name":"Qwen3-0.6B-GGUF","ctx_size":0}'

# 3. Does a plain /load re-pin the ctx of an already-loaded model?
#    If yes, the unload->poll->load->poll settle machinery is over-engineering.
curl -sS -X POST localhost:13305/api/v1/load -H 'Content-Type: application/json' \
  -d '{"model_name":"Gemma-4-E4B-it-GGUF","ctx_size":16384}'
curl -sS localhost:13305/api/v1/health | python -m json.tool

# 4. Does Lemonade honour llama.cpp-native repeat_penalty via extra_body?
curl -sS -X POST localhost:13305/api/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"Gemma-4-E4B-it-GGUF","messages":[{"role":"user","content":"hi"}],"repeat_penalty":1.2}'

# 5. Real /system-info `recipes` shape — the init skip-if-installed logic depends on it.
curl -sS localhost:13305/api/v1/system-info | python -m json.tool
```
