---
type: plan
source-issue: 1264
repo: amd/gaia
created: 2026-06-02
status: complete
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 2
test_command: ".venv/bin/python -m pytest tests/integration/test_never_auto_send.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1264-never-auto-send
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Issue #1264 — feat(email): send with confirmation (never auto-send)

## Goal
Sending email always requires explicit confirmation; the agent never
auto-sends — across **every** surface. The gates already exist and are
individually tested at three layers; this issue's deliverable is a single
**consolidated cross-surface regression guard** plus a user-facing doc note.
This is consolidation, NOT re-implementation.

## Existing gates (consumed, not rewritten)
1. **Agent tool layer** — `Agent._execute_tool`
   (`src/gaia/agents/base/agent.py:1655`) routes any tool in
   `TOOLS_REQUIRING_CONFIRMATION` (`agent.py:44` — includes `send_draft`,
   `send_now`, `forward_message`) through `console.confirm_tool_execution()`.
   A withheld confirmation returns `{"status": "denied"}` and the tool impl
   is never invoked. Real email send tools live in
   `src/gaia/agents/email/tools/reply_tools.py`.
   Existing test: `tests/unit/agents/test_email_agent_confirmation.py`.
2. **REST layer (#1229)** — `POST /v1/email/send`
   (`src/gaia/api/email_routes.py:send_email`) raises HTTP 403 unless a
   single-use, payload-bound `ConfirmationStore` token is supplied.
   Existing test: `tests/test_api.py::TestEmailSendConfirmationGate`.
3. **MCP-stdio layer (#1104)** — `EmailTriageMCPAgent`
   (`src/gaia/mcp/servers/email_mcp.py`) enforces its own `ConfirmationStore`;
   a tokenless `send_email` returns `{"sent": False, "error": ...}`, never a
   send. Existing test: `tests/mcp/test_email_mcp_stdio_parity.py`.

## Acceptance criteria
- [ ] Sending requires explicit confirmation; never auto-sends (all surfaces).
- [ ] A server-side test that the send path is rejected without a token.
- [ ] (Deliverable) One consolidated test asserting the invariant at agent +
      REST + MCP layers together, so no future change can quietly drop a gate
      on one surface.
- [ ] (Deliverable) A scoped "Sending email — safety" doc note in
      `docs/guides/email.mdx` documenting the never-auto-send guarantee.

## Approach (TDD)
1. Write `tests/integration/test_never_auto_send.py` FIRST. It exercises all
   three gates **in-process** (deterministic, no Lemonade, no live send, no
   stdio subprocess):
   - **Agent**: minimal `Agent` subclass (`skip_lemonade=True`) with a
     sentinel `send_now` tool + a denying console → assert `_execute_tool`
     returns `denied` and the sentinel impl is never called. Plus a positive
     control: a non-gated tool runs without confirmation. Plus a binding
     assertion that the real email send tool names (`send_draft`, `send_now`,
     `forward_message`) are in `TOOLS_REQUIRING_CONFIRMATION`.
   - **REST**: call `email_routes.send_email()` directly via `anyio.run` —
     tokenless → 403; draft→send with the minted token (fake backend) → sent.
     Bait-and-switch (token bound to a different payload) → 403.
   - **MCP**: instantiate `EmailTriageMCPAgent` and call
     `execute_mcp_tool("send_email", ...)` — tokenless → `{"sent": False}`;
     draft→send handshake → sent.
   - **Cross-surface meta-assert**: the same tokenless payload is rejected by
     every surface (collect the three rejections and assert all hold).
   - **NOTE / gotcha**: must NOT spawn the stdio MCP subprocess — a local
     `tests/unit/mcp/__init__.py` shadows the installed `mcp` SDK once
     `repo_root` is on `sys.path`. In-process exercise sidesteps it and keeps
     the guard fast.
2. Add the doc note (new `## Sending email — safety` section in
   `docs/guides/email.mdx`, before "Privacy guarantees"; do NOT touch the
   Setup/connector-credentials or CLI-reference sections — a parallel PR owns
   those).
3. Green; run the three pre-existing suites individually + the new test.

## Lane boundary
- OWN: `tests/integration/test_never_auto_send.py`, a scoped subsection in
  `docs/guides/email.mdx`, and ONLY a gate fix if a real bypass is found.
- DO NOT rewrite `email_routes.py`, `email_mcp.py`, `agent.py`, or the
  existing confirmation tests.

## Eval trigger
None. No LLM-affecting code path is touched (test + doc only; the agent-layer
gate is mechanism-level and uses a sentinel tool, not a prompt change).

## Risks
- `mcp`-SDK shadowing if the stdio subprocess were used → avoided by
  in-process MCP exercise.
- `EmailTriageAgent.__init__` hard-wires Lemonade (no `skip_lemonade`
  passthrough) → avoided by using a minimal `Agent` subclass for the
  agent-layer mechanism test, with a separate import-only assertion binding
  the real email tool names to the gate set.
