---
type: plan
source-issue: 1104
repo: amd/gaia
title: "feat(mcp): add stdio transport to AgentMCPServer"
created: 2026-06-02
status: in-progress
work_type: code-feature
complexity: complex
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 4
test_command: ".venv/bin/python -m pytest tests/mcp/ tests/test_api.py -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1104-mcp-stdio
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# feat(mcp): add stdio transport to AgentMCPServer

## Problem

`AgentMCPServer.start()` hard-codes `transport="streamable-http"`
(`src/gaia/mcp/agent_mcp_server.py:211`). There is no way to expose a GAIA
agent over MCP **stdio** — the transport every desktop MCP client (VSCode,
Copilot, Claude Desktop) launches by default — and no test proving the agent
returns the *same* structured output over MCP that it returns over the REST
surface (#1229).

## Goal / Acceptance criteria

- AC1 — The agent is invocable over MCP **stdio** with the same capabilities
  as REST.
- AC2 — A parity test invokes the **same operation** over REST
  (`EmailTriageService`) and over the new MCP **stdio** path for a **fixed
  fixture** and asserts **byte-identical structured output**.
- AC-send — The send-confirmation gate (#1264) is enforced on the MCP side
  too, with its **own** `ConfirmationStore` instance (no shared state with the
  REST process-wide store).

## Design

Parity is *guaranteed by construction*: both surfaces call the **same
FastAPI-free `EmailTriageService`** from `src/gaia/api/email_routes.py`. The
MCP path never re-implements triage — it validates the frozen #1262 contract,
calls `EmailTriageService.triage_request(...)`, and serializes the contract
response. Identical service + identical contract ⇒ identical output.

Two pieces of new code, both in my lane:

1. **`AgentMCPServer` stdio transport** (`src/gaia/mcp/agent_mcp_server.py`):
   add a `transport: Literal["streamable-http","stdio"] = "streamable-http"`
   constructor param. In `start()`, dispatch on it. In stdio mode the startup
   banner must NOT touch stdout (stdio framing requires stdout to carry only
   JSON-RPC bytes) — route it to stderr / skip it. Host/port are no-ops in
   stdio mode. FastMCP supports `self.mcp.run(transport="stdio")` natively.

2. **Email MCP wrapper** (`src/gaia/mcp/servers/email_mcp.py`): a deterministic
   `MCPAgent` subclass (`EmailTriageMCPAgent`) that:
   - constructs offline (`skip_lemonade=True`, `silent_mode=True`) — triage is
     deterministic, no Lemonade needed;
   - exposes three MCP tools mirroring the REST surface: `triage_email`,
     `draft_reply`, `send_email`;
   - routes `triage_email` straight through `EmailTriageService`;
   - holds its **own** `ConfirmationStore`; `send_email` rejects (structured
     error, never raises a send) without a valid payload-bound token, exactly
     like the REST 403 gate;
   - `email_routes` is imported **read-only**. I do NOT modify it.
   - plus a `start_email_mcp(transport=...)` launcher + `__main__` entry the
     parity test spawns as a stdio subprocess.

## TDD

1. **FAILING parity test** under `tests/mcp/test_email_mcp_stdio_parity.py`:
   spawn `python -m gaia.mcp.servers.email_mcp --transport stdio` as a
   subprocess, drive `tools/list` + `tools/call triage_email` over the MCP
   Python SDK stdio client, and assert the result equals
   `EmailTriageService().triage_request(...)` for the SAME fixed fixture
   (single + thread). No Lemonade, no network. Also assert the send gate
   rejects without a token and accepts the draft→send handshake.
2. Implement (1) + (2) above until green.
3. Self-review focused on: stdout cleanliness in stdio mode, the send gate
   cannot be bypassed over MCP, no silent fallbacks.

## Lane boundary

OWN: `src/gaia/mcp/agent_mcp_server.py`, `src/gaia/mcp/servers/email_mcp.py`,
`tests/mcp/`. Read-only import of `src/gaia/api/email_routes.py`. Do NOT touch
`email_routes.py`, `agent.py`, `contract.py`, `connectors/`, email `tools/`.

## Out of scope / not done here

- Wiring a `gaia <agent> --mcp` CLI flag (the issue comment floats it; it is
  not an acceptance criterion). Left for a follow-up.
- Switching the live `EmailTriageAgent` base class to `MCPAgent`.
