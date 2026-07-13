---
type: plan
source-issue: 1232
repo: amd/gaia
title: "refactor(email): post-demo cleanup — tools_count, envelope dedup, token-path unify"
created: 2026-07-13
status: draft
work_type: code-refactor
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 18
test_command: "python -m pytest hub/agents/python/email/tests/ -x -q"
build_command: "uv pip install -e . -e hub/agents/python/email  # into a per-worktree venv (see Env Setup)"
lint_command: "python util/lint.py --all"
branch: tmi/1232-email-refactor-cleanup
checkpoint_redirects: 0
agents_used: [planning, reflection, execution, validation]
reflection_verdict: amended
---

# Issue #1232 — Email agent post-demo cleanup (3 independent refactors)

An internal refactor of the packaged email agent (`hub/agents/python/email/gaia_agent_email/`).
No LLM-affecting surface changes (no system-prompt, tool-docstring, tool-schema, or model change) →
**`gaia eval agent` is NOT required**; unit + integration tests are the gate.

**Behavior-change caveat (from adversarial reflection):** Parts 1 & 2 are behavior-neutral. **Part 3
is NOT** — switching Calendar to `get_access_token_sync` *adds* an OAuth-scope pre-flight check
Calendar never had, changing one broken-state HTTP code 502→403. This is a strict fail-loud
improvement (no privilege escalation, identical scope set), aligned with GAIA's "No Silent Fallbacks"
rule — but it is user-observable and must be documented + tested (see Part 3 below).

Three logically distinct changes, each its own checkpoint boundary:
1. **tools_count metadata** — fix + anti-drift guard test spanning BOTH pinned copies.
2. **envelope dedup** — extract 12 consumers onto one `tools/envelope.py`.
3. **token-path unify** — Calendar joins Gmail on `get_access_token_sync`.

---

## Ground-truth findings (evidence, verified 2026-07-13; ✎ = corrected in reflection)

### Part 1 — `tools_count` (the issue's "~37" premise is STALE)
- `hub/agents/python/email/gaia_agent_email/__init__.py:123` hardcodes `tools_count=52` in
  `build_registration()`. There are **exactly 52 `@tool` decorators** across the 12 registered email
  tool-mixin modules (`organize 15, read 8, calendar 6, reply 5, schedule 4, preference 4, delete 3,
  voice 2, phishing 2, summarize 1, profile 1, followup 1 = 52`), all registered 1:1 by the 12
  `self._register_*_tools()` calls in `agent.py:544-563`. So `52` already matches the stable email-tool
  set; the issue's "~37" was stale by planning time.
- ✎ **SECOND UNGUARDED COPY:** `hub/agents/python/email/gaia-agent.yaml:16` **also** hardcodes
  `tools_count: 52`, independent of `__init__.py`. The npm CHANGELOG documents this dual-pin biting the
  project before (a stale `6` had to be fixed in BOTH files). `.github/workflows/release_agent_email.yml`
  ships the YAML value verbatim to the public hub/npm catalog with **no validation**. Both copies must be
  guarded, or the fix is incomplete (CLAUDE.md: "update EVERY doc that describes it").
- ✎ **Tool enumeration:** `@tool` populates the module-global `gaia.agents.base.tools._TOOL_REGISTRY`.
  The `_tools_registry` property is on the **base** class at `src/gaia/agents/base/agent.py:717-726`
  (NOT `gaia_agent_email/agent.py:899` — earlier citation was wrong). It returns `self._instance_tools`
  if `_snapshot_tools()` was called, **else the live global `_TOOL_REGISTRY`**.
- ✎ **`EmailTriageAgent` never snapshots:** its `_register_tools()` (`agent.py:544-563`) does
  `_TOOL_REGISTRY.clear()` first (line ~549) but **never calls `self._snapshot_tools()`** — the outlier
  vs. 7+ other agents (chat, builder, analyst, browser, doc-search, word-count, connectors-demo) that
  all snapshot. The `.clear()` makes the count deterministic *immediately after construction in a suite
  that builds no other agent*, but the registry stays a live alias to the shared global — a latent
  cross-agent bleed bug and a fragile foundation for the guard test.
- **Memory tools are conditional:** `agent.py:563` calls `register_memory_tools()`, which adds **5**
  tools **only when memory is enabled** (`memory.py:2135` skips when `_memory_store is None`, e.g.
  `GAIA_MEMORY_DISABLED=1` / Lemonade down). So the live registry is 52 (memory off) or 57 (on). `52`
  correctly pins the **email**-tool count; the guard test MUST run with memory off and assert that
  precondition.
- No `AGENT_TOOLS_COUNT` class attribute exists. Consumers of the metadata: `src/gaia/ui/routers/agents.py:123`
  (UI) and the hub manifest pipeline (`src/gaia/hub/manifest.py`).

### Part 2 — envelope dedup (11 identical defs + 1 transitive importer = 12 consumers)
`_envelope_ok`/`_envelope_err` are defined **byte-for-byte identically** in 11 files under `tools/`
(`read_tools.py:98 summarize_tools.py:89 phishing_tools.py:49 delete_tools.py:29 reply_tools.py:83
profile_tools.py:91 voice_tools.py:38 organize_tools.py:32 schedule_tools.py:42 preference_tools.py:54
calendar_tools.py:45`):

```python
def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)

def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})
```

- ✎ **12th consumer:** `tools/followup_tools.py:34-38` has NO local def — it imports the helpers from
  `read_tools.py`: `from gaia_agent_email.tools.read_tools import (_envelope_err, _envelope_ok,
  extract_sender_email)`. If ignored, `read_tools.py` silently becomes an undocumented re-export shim and
  AC2's "single-sourced" goal is not met. Repoint its envelope import to `envelope.py` too (keep
  `extract_sender_email` from `read_tools` — a legitimate separate dependency).
- ✎ **Call-site count is ~183** (not "~60"); doesn't change the approach (import scheme is count-agnostic).
- `tools/__init__.py` exists (empty). Precedent for plain shared-helper modules inside `tools/` already
  exists (`triage_heuristics.py`, `llm_triage.py`, cross-imported by siblings) — so `tools/envelope.py`
  is the right, conventional home.
- Out of scope (verified, do NOT touch): `src/gaia/ui/email_sidecar/proxy_agent.py:92` has the same
  helper shape but lives in **core** `gaia.ui` (dependency direction forbids importing the hub package).
  Mention in the PR so it isn't later mistaken for an incomplete dedup.

### Part 3 — token-path unify (auth-contract-sensitive; adds a fail-loud check)
- Gmail `gmail_backend.py:711 _get_gmail_token()` → `get_access_token_sync(provider="google",
  agent_id=AGENT_NAMESPACED_ID, scopes=list(GMAIL_SCOPES))` (from `gaia.connectors.api`) → token **string**.
- Calendar `calendar_backend.py:247 _get_calendar_token()` → `get_credential_sync("google",
  agent_id=AGENT_NAMESPACED_ID, required_scopes=list(CALENDAR_SCOPES))["access_token"]` (from
  `gaia.connectors.handler`) → **dict**, only `["access_token"]` used.
- ✎ **The two paths are NOT equivalent** (reflection corrected the plan's earlier claim):
  - **Shared:** both do the per-agent grant check and raise the same `AuthRequiredError(AGENT_NOT_GRANTED)`
    (`api.py:120-127`, `handler.py:154-163`).
  - **api.py ONLY:** `get_access_token` (`api.py:132-151`) additionally loads the stored connection and
    raises `AuthRequiredError(CONNECTION_MISSING_SCOPES)` if `stored["scopes"]` doesn't cover the request —
    **before** any network call. The handler path (`oauth_pkce.py:89-108` → `get_or_refresh`) has **no such
    check** (echoes requested scopes back unvalidated). `CONNECTION_MISSING_SCOPES` is raised in exactly one
    place repo-wide: `api.py:147`.
  - **Effect of the swap:** in the broken state (agent granted calendar, but the stored connection lacks
    calendar scope) the OLD Calendar path returned a token → Google 403 → email API maps to **HTTP 502**;
    the NEW path fails pre-flight with `CONNECTION_MISSING_SCOPES` → **HTTP 403** + actionable reconnect
    message, no network call. **Security review: strict-superset-safe** — same immutable `CALENDAR_SCOPES`
    (`scopes.py:43-46`), same connector, no scope widening, no path returns a token the old path refused.
    It only *adds* a fail-loud check → desirable, and aligned with the repo's "No Silent Fallbacks" rule.
- `calendar_backend.py` already imports `AGENT_NAMESPACED_ID, CALENDAR_SCOPES` (`:26`). Exactly ONE
  `get_credential_sync` usage (`:249`) + its import (`:28`). Callers of `_get_calendar_token`
  (`config.py:428`, `api_routes.py:1359`) pass it by reference and use the bare `str` return — unaffected
  by dropping the dict wrapper (verified).
- **Scope boundary (do NOT over-reach):** Outlook resolvers `outlook_backend.py:618`,
  `outlook_calendar_backend.py:353` stay on `get_credential_sync("microsoft", ...)` — issue scope is
  Google (Gmail vs Calendar); the api.py `get_provider`/`load_connection` layer is unverified for
  microsoft. Record the real framework-level follow-up: `OAuthPkceHandler.get_credential` lacks the
  scope-coverage check `api.get_access_token` has — a `src/gaia/connectors/` fix, not this p3.

---

## Acceptance criteria (concrete, testable; test-author writes them FIRST)

**AC1 — tools_count accurate & drift-guarded across BOTH pinned copies.**
- Extend `hub/agents/python/email/tests/test_email_agent.py::test_build_registration_shape` (it already
  builds `build_registration()`): with memory forced off (`GAIA_MEMORY_DISABLED=1`, set/restored in-test —
  there is NO conftest), instantiate `EmailTriageAgent`, **assert `agent._memory_store is None`** (the
  precondition — without it a live-Lemonade dev box adds 5 memory tools and the count is wrong), derive
  `live = len(agent._tools_registry)` from the live registry (NOT a second literal), and assert
  `build_registration().tools_count == live`.
- **Also** parse `hub/agents/python/email/gaia-agent.yaml` and assert its `tools_count` equals the same
  `live` value (close the second-copy drift). Adding a `@tool` without bumping BOTH files must fail this.
- After the fix, both `__init__.py:123` and `gaia-agent.yaml:16` equal the verified live email-tool count.

**AC2 — envelope helpers single-sourced & output-identical (all 12 consumers).**
- `tools/envelope.py` exists, defines `_envelope_ok`/`_envelope_err` verbatim (see Increment A).
- Output tests: `_envelope_ok` → `{"ok": true, "data": ...}` incl. `default=str` behavior
  (`json.loads(_envelope_ok({"when": datetime(2026,7,13)}))["data"]["when"] == str(datetime(2026,7,13))`);
  `_envelope_err` → `{"ok": false, "error": ...}`.
- **Wiring test (all 12, not one example):** for each of the 11 def-modules + `followup_tools`, assert
  `module._envelope_ok is envelope._envelope_ok` and `..._err is envelope._envelope_err`.
- **AST anti-regression:** glob every `tools/*.py` except `envelope.py`/`__init__.py`; assert **zero**
  `FunctionDef` named `_envelope_ok`/`_envelope_err` remain (catches a future 12th copy-paste too).
- Full existing email suite stays green (no tool changed its envelope output).

**AC3 — Calendar & Gmail share one token path, error contract pinned.**
- `calendar_backend._get_calendar_token` calls `get_access_token_sync`; no `get_credential_sync` remains
  in `calendar_backend.py`.
- Mock **at the import site** `patch("gaia_agent_email.calendar_backend.get_access_token_sync")` (NOT
  `gaia.connectors.api...` — wrong target = vacuous pass). Assert `_get_calendar_token()` returns the token
  string and calls with `provider="google", agent_id=AGENT_NAMESPACED_ID, scopes=list(CALENDAR_SCOPES)`.
- **Error-contract parity:** side_effect `AuthRequiredError(AGENT_NOT_GRANTED, ...)` → assert it propagates
  unchanged (parity with Gmail).
- **New-behavior test:** side_effect `AuthRequiredError(CONNECTION_MISSING_SCOPES, ...)` → assert it
  propagates (documents the added pre-flight). These 2 unit tests are Part 3's ONLY code-path coverage
  (the suite's `_FakeCalendarBackend` is injected above `LiveCalendarBackend` and never hits this).

**AC0 — no regressions.** `python util/lint.py --all` clean on touched files; full email suite green.

---

## Implementation increments (checkpoint boundaries)

Order **Part 2 → Part 3 → Part 1** (independent; ordered for reviewer clarity).

### Increment A — envelope dedup (Part 2)
1. Create `tools/envelope.py` — verbatim names, house convention (cf. `_TOOL_REGISTRY` imported
   unaliased across 13 files incl. this agent's `agent.py:73`); no public-rename/alias scheme:
   ```python
   """Shared JSON envelope helpers for email agent tools (single source — #1232).

   Note: unrelated to the "envelope" REST/MCP request wrappers in contract.py.
   """
   import json
   from typing import Any

   def _envelope_ok(data: Any) -> str:
       return json.dumps({"ok": True, "data": data}, default=str)

   def _envelope_err(message: str) -> str:
       return json.dumps({"ok": False, "error": message})
   ```
2. In each of the **11 def-modules**: delete the two local defs, add
   `from gaia_agent_email.tools.envelope import _envelope_ok, _envelope_err`. Call sites unchanged.
3. In `followup_tools.py` (**12th consumer**): split the import — envelope helpers from
   `.envelope`, keep `extract_sender_email` from `read_tools`.
4. **Per-file unused-import sweep:** after removing local defs, drop any now-unused `import json` / `Any`
   (`flake8 F401`). Most modules use them heavily — verify each, don't assume.
5. Green `test_command` + `lint_command`. **Checkpoint.**

### Increment B — token-path unify (Part 3)
1. `calendar_backend.py:28` — replace `from gaia.connectors.handler import get_credential_sync` with
   `from gaia.connectors.api import get_access_token_sync`. Keep `ConnectorsError` (`:27`) and other
   handler imports if still used; drop only `get_credential_sync` (confirm no other usage).
2. Rewrite `_get_calendar_token`:
   ```python
   def _get_calendar_token() -> str:
       """Return a Calendar access token via the standard grant-checked path."""
       return get_access_token_sync(
           provider="google",
           agent_id=AGENT_NAMESPACED_ID,
           scopes=list(CALENDAR_SCOPES),
       )
   ```
3. Green tests + lint. **Checkpoint.**

### Increment C — tools_count fix + guard (Part 1)
1. In the provenance-verified worktree venv, instantiate `EmailTriageAgent` (memory off) and read the live
   email-tool count. Confirm it is 52 (expected).
2. If either `__init__.py:123` or `gaia-agent.yaml:16` is wrong, correct it to the verified count. If both
   already correct, leave them and add a one-line `# guarded by tests/test_email_agent.py (#1232)` comment.
3. ✎ **Snapshot fix (verify-first, justified in-scope):** grep the email package for `_tools_registry`
   usage; if nothing relies on it reflecting *later* live-global mutations (it shouldn't), add
   `self._snapshot_tools()` as the final line of `_register_tools()` (`agent.py`, after ~:563) — aligns
   `EmailTriageAgent` with every other agent, fixes the latent cross-agent registry-bleed bug, and makes
   AC1's guard correct by construction. If any reliance is found, SKIP the snapshot and rely on the memory
   precondition assertion + `.clear()` for determinism (note the decision in the handoff).
4. Ensure the AC1 guard (both files) passes. Green tests + lint. **Final checkpoint.**

---

## Env Setup — AVOID the worktree editable-install trap (critical)

The shared `/Users/tomasz/src/amd/gaia/.venv` may editable-install `gaia_agent_email` (and core `gaia`)
from a **stale sibling worktree** → tests run against the WRONG code → false green. In this isolated
worktree, BEFORE any test:

1. `uv venv .venv-wt && source .venv-wt/bin/activate` (or the repo's install method); install BOTH editable
   from THIS worktree: `uv pip install -e . -e hub/agents/python/email`.
2. **Verify provenance of BOTH packages** (the cited memory says check both):
   ```
   python -c "import gaia, gaia_agent_email; print(gaia.__file__); print(gaia_agent_email.__file__)"
   ```
   Both paths MUST resolve inside `.../worktrees/<this-worktree>/...`, not a sibling `agent-*` worktree.
   Reinstall + re-verify if not.
3. macOS keyring hang guard (connectors import touches keyring): export `PYTHON_KEYRING_BACKEND=null`.
4. **If CI later goes red on a path this worktree venv reported green, re-verify `__file__` provenance
   BEFORE writing the red off as infra flake.**

Refs: memory `project_worktree_editable_install_trap.md`, `project_email_benchmark_headless_gotchas.md`.
Packaging is safe (verified): `packaging/freeze.py` uses `--collect-submodules gaia_agent_email`
(whole-tree) and `pyproject.toml` `packages.find include=["gaia_agent_email*"]`, so `tools/envelope.py`
is auto-bundled — no manifest edit needed.

---

## Test plan (unit + integration; real-world degraded — justified)
- **Unit (local):** AC1 (in `test_email_agent.py`), AC2 (new `hub/agents/python/email/tests/test_envelope.py`),
  AC3 (new `hub/agents/python/email/tests/test_calendar_token_path.py`). Use FULL paths — the increment's
  own `test_command` only globs `hub/agents/python/email/tests/`.
- **Integration (local):** full `python -m pytest hub/agents/python/email/tests/ -q` green against the
  provenance-verified worktree venv.
- **Real-world:** **degraded to a local end-to-end smoke, no hardware/OAuth run** — this is an internal
  refactor with no new user feature; the token-path change's contract is covered by the mocked AC3 tests.
  Smoke = instantiate the agent, confirm the guarded tool count, and exercise a **calendar** tool (name it
  explicitly — since Part 3's only other coverage is the two AC3 unit tests) to confirm a well-formed
  envelope via the shared module. State this degradation + the 502→403 change in the PR.

## Risks & mitigations
- **P2 unused-import churn** → per-file F401 sweep; `util/lint.py --all` gate.
- **P2 missed 12th consumer** → `followup_tools.py` repointed + AST anti-regression test.
- **P3 error-contract change (502→403)** → AC3 `CONNECTION_MISSING_SCOPES` test + PR call-out; Outlook
  untouched.
- **P1 dual-pin drift** → guard test pins BOTH `__init__.py` and `gaia-agent.yaml`.
- **P1 registry non-determinism** → memory-off precondition assertion + (verify-first) `_snapshot_tools()`.
- **False green from stale editable install** → mandatory `gaia` + `gaia_agent_email` `__file__` check.

## Out of scope
- Batch-counter-reset fix (stays in #1106).
- Outlook/Microsoft token-path conversion (unverified microsoft support on api.py path).
- Framework follow-up: add scope-coverage check to `OAuthPkceHandler.get_credential` (`src/gaia/connectors/`).
- Core `proxy_agent.py` envelope copy (different distribution unit; dependency direction forbids sharing).
- Any prompt/model/tool-schema change (none → no eval).

## Adversarial Reflection
**Verdict: amended** (panel: 5× amend, 1× solid/security). Six `model: sonnet` finders reviewed the plan
against real code; synthesis auto-amended all Critical findings (none required a human decision).

**Strengths confirmed by the panel:** envelope copies byte-identical; `tools/envelope.py` placement matches
house convention; Outlook-left-untouched is correct minimal scope; no Gmail/Calendar helper-collapse is the
right restraint (both are injectable seams); hardcode-plus-guard-test is the correct `tools_count`
architecture (registration must stay lazy); packaging auto-bundles the new module; security — no scope
widening or escalation.

**Critical findings folded in:** (A) `followup_tools.py` 12th envelope consumer; (B) Part 3 adds a
`CONNECTION_MISSING_SCOPES` pre-flight (502→403) — not behavior-neutral, but a strict fail-loud
improvement; (C) `EmailTriageAgent` never `_snapshot_tools()` — add it (verify-first) so AC1's guard is
robust; (D) second unguarded `tools_count: 52` in `gaia-agent.yaml` — guard both.

**Advisory folded in:** simplify to verbatim `_envelope_*` names (no alias); fold AC1 into the existing
test; fix the `_tools_registry` citation (`base/agent.py:717`); pin AC3 mock to the import site; verify
BOTH packages' `__file__`; name a calendar tool in the smoke; `missing_scopes` precision differs on
AGENT_NOT_GRANTED (message unaffected — PR note); mention the core `proxy_agent.py` copy in the PR.
