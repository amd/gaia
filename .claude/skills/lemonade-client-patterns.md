---
name: lemonade-client-patterns
description: Patterns, gotchas, and conventions for threading changes through LemonadeClient, its callers, and the GAIA test suite. Apply when modifying lemonade_client.py, its providers, VLMClient, UI routers, or any file that calls Lemonade HTTP endpoints.
---

# Lemonade Client Patterns

## Context
`src/gaia/llm/lemonade_client.py` is GAIA's primary Lemonade HTTP client (~4000 lines). Changes to its interface ripple through `LemonadeProvider`, `VLMClient`, Agent UI routers, `_chat_helpers.py`, `server.py`, and `agents/base/agent.py`. The test suite mixes `responses` (for `requests`-based calls) and `mocker.patch` on `httpx` (for async calls).

## Key Patterns

### HTTP call sites — not just `_send_request`
`LemonadeClient` has a central `_send_request` but also 4 direct `requests.post` bypass sites (chat_completions non-stream, completions non-stream, pull_model SSE, `responses`/load_model SSE) and 2 `OpenAI(...)` constructor sites. Any change that must affect ALL outbound calls (e.g., adding an auth header) must be applied to ALL six families, not just `_send_request`.

### Factory must mirror `__init__`
`create_lemonade_client()` at the bottom of `lemonade_client.py` is a convenience factory. When adding a new `LemonadeClient.__init__` parameter, update the factory to accept and forward it — callers that use the factory (like CLI entry points) won't pick up the new param otherwise.

### Module-level helpers should be PUBLIC (no underscore)
Helpers shared across packages (`vlm_client.py`, `ui/routers/system.py`, `ui/_chat_helpers.py`, `ui/server.py`, `agents/base/agent.py`) must be public-named (no leading `_`). Leading underscore signals "package-internal" and creates confusion. Precedent: `system.py` already imports `DEFAULT_CONTEXT_SIZE` from `lemonade_client`.

### `agents/base/agent.py` — deferred imports are intentional
The base `Agent` class deliberately imports `gaia.llm.lemonade_client` ONLY inside `try:` blocks inside methods (never at module level). This preserves the LLM-backend-agnostic layering invariant so agents can run with Claude/OpenAI backends without loading Lemonade. **Do not add module-level imports from `gaia.llm.*` to this file.**

### VLMClient does NOT resolve env vars itself
`VLMClient` forwards `api_key=` raw to `LemonadeClient`, which does the single canonical resolution. VLMClient has no direct HTTP calls (delegates everything to `self.client`), so pre-resolving env vars there violates single-source-of-truth.

### 401 errors — use fixed-string messages, never `response.text`
Misconfigured reverse proxies can reflect the `Authorization` header back in a 401 response body. If `response.text` is included in a user-visible error message, the API key leaks to CLI output, logs, and bug reports. Always use a fixed-string 401 error that names `LEMONADE_API_KEY` without including the response body.

### OpenAI SDK auth error handling
`openai.AuthenticationError` is NOT a subclass of `openai.APIError` in openai>=1.0. It must be caught separately, and the `except openai.AuthenticationError:` branch MUST come BEFORE the generic `except (openai.APIError, openai.APIConnectionError, openai.RateLimitError)` branch — Python evaluates except clauses in order and `AuthenticationError` would otherwise fall through to the generic one. Never include `str(e)` from an OpenAI exception (the SDK may stringify the request including the Authorization header).

## Important Files & Locations

- `src/gaia/llm/lemonade_client.py` — Primary client (~4000 lines); `_send_request` is the central chokepoint but 4 bypass sites exist
- `src/gaia/llm/providers/lemonade.py` — `LemonadeProvider.__init__` uses `backend_kwargs` dict to forward to `LemonadeClient`; add new params with `if param is not None: backend_kwargs["param"] = param`
- `src/gaia/llm/vlm_client.py` — `VLMClient.__init__` uses deferred import of `LemonadeClient`
- `src/gaia/ui/routers/system.py` — already imports `DEFAULT_CONTEXT_SIZE` from `lemonade_client` (established cross-package import precedent)
- `src/gaia/ui/_chat_helpers.py` — 4 Lemonade-bound httpx call sites: auto-title POST (~337), health GET (~1006, ~1058), stats GET (~2289). (Line numbers drift — grep the call, don't trust the offset.)
- `src/gaia/ui/server.py` — 2 health probe httpx GET sites (~299, ~311)
- `src/gaia/agents/base/agent.py` — `_is_loaded_ctx_too_small()` (~2177) — DEFERRED import pattern
- `tests/test_lemonade_client.py` — uses `responses` library for `requests` interception; `TestLemonadeClientMock` class
- `docs/.env.example` — This is a Mintlify/docs-proxy config file, NOT GAIA's env var file. GAIA env vars go in `.env.example` at the repo root.

## Conventions

- Env var resolution helpers live in `lemonade_client.py` as public module-level functions (single source of truth)
- Empty/whitespace env values are treated as unset: `value.strip() or None`
- OpenAI SDK sites use `api_key=self.api_key or "lemonade"` placeholder when unauthenticated — the SDK rejects `None`/`""` with `OpenAIError`; Lemonade ignores the placeholder value
- Auth headers: `{"Authorization": f"Bearer {api_key}"}` — omit the header entirely when key is None (don't send `Bearer `)
- Log key presence at DEBUG level only: `self.log.debug("Lemonade API key configured")` — NEVER log the value

## Gotchas & Learned the Hard Way

### Deferred imports break the standard patch target
When a class uses deferred imports (import inside `__init__` or a method body), patching via the IMPORTING module fails with `AttributeError`. Patch at the SOURCE module where the symbol is defined.

**Wrong:** `mocker.patch("gaia.llm.vlm_client.LemonadeClient")` (deferred import; the name doesn't exist at module level)

**Right:** `mocker.patch("gaia.llm.lemonade_client.LemonadeClient")` (patches the definition, affects all users)

### `assertLogs` doesn't escalate child logger levels
`self.assertLogs("gaia", level=logging.DEBUG)` does NOT force child loggers like `gaia.llm.lemonade_client` to DEBUG — they inherit their configured level. To capture DEBUG messages from a specific child logger in tests:

```python
lc_logger = logging.getLogger("gaia.llm.lemonade_client")
original_level = lc_logger.level
lc_logger.setLevel(logging.DEBUG)
try:
    with self.assertLogs("gaia", level=logging.DEBUG) as cm:
        # ... test body
finally:
    lc_logger.setLevel(original_level)
```

### SSE tests hang without a terminating body
Tests that call SSE-generating methods (`pull_model`, `load_model`) via `responses` library will block forever on `iter_lines()` unless the mock body is a complete SSE event ending with `\n\n`:

```python
responses.add(
    responses.POST, f"{base_url}/pull",
    body=b"event: complete\ndata: {}\n\n",
    content_type="text/event-stream",
    stream=True,
)
gen = client.pull_model("model")
next(gen)  # consume one item; don't iterate fully or it blocks
```

### `_ensure_model_loaded` makes its own network calls
In `test_401_does_not_trigger_auto_download_retry`-style tests, forgetting to mock `_ensure_model_loaded` causes the test to fail because `_ensure_model_loaded` itself calls `get_status()` → `list_models()`, which makes HTTP requests that can trigger model loading. Always patch `_ensure_model_loaded` when testing `chat_completions` error paths.

### Pre-existing test failures (not regressions)
Several tests in `tests/test_lemonade_client.py` fail on `main` and are pre-existing failures (not regressions from new work):
- `test_pull_model_stream` — `progress_callback` kwarg removed
- `test_get_required_models_*` — Qwen→Gemma model name drift
- `test_download_agent_models_*` — deprecated API

Verify these fail on `main` before assuming new work caused them.

### `docs/.env.example` is NOT the GAIA env vars file
`docs/.env.example` is a Mintlify/docs-proxy configuration file. GAIA env variable documentation belongs in `.env.example` at the repo root.

---
_Auto-generated by /learn | Confidence: 0.92 | Last updated: 2026-05-19_
_Source conversations: 1 | Version: 1_
