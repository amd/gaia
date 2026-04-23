# AXIS × GAIA Integration Report

> Deep code review and scoping of integrating AXIS (axis-sandbox/axis) into GAIA (amd/gaia)
> Reviewed: 2026-04-20 | AXIS v0.3.5 | GAIA v0.17.3

---

## Executive Brief

GAIA is AMD's local AI agent framework (capability layer). AXIS is AMD's OS-native agent sandbox runtime (security layer). Both are AMD-owned, target the same Ryzen AI hardware, and have zero functional overlap. The integration is straightforward to justify: GAIA can do a lot but is not safe to run autonomously; AXIS is safe but has no agent capabilities.

### Value Proposition

| Pillar | Without Integration | With Integration |
|---|---|---|
| **Security** | App-layer path checks, confirmation prompts | Landlock + seccomp-BPF, default-deny, OPA network policy |
| **GPU isolation** | Agents share GPU with no per-agent VRAM limits | Per-agent VRAM quotas via HIP Remote, denied device-reset/IPC |
| **Network egress** | Unrestricted — any agent can call any endpoint | Per-agent OPA-enforced egress allowlist at 59K decisions/sec |
| **Credential safety** | Credentials live inside the agent process | Injected at proxy boundary, never in sandbox process |
| **Audit** | No structured logging of tool calls | OCSF audit events per sandbox, streamed via WebSocket |
| **Privacy** | Agents may exfiltrate local data to cloud | Policy-enforced network block; local Lemonade stays local |
| **Multi-agent safety** | All agents share a global tool registry (race condition) | Out-of-process agents: isolated registries, no shared state |
| **MCP tool safety** | MCP subprocesses inherit parent's full privileges | Each MCP server sandboxed with a scoped policy |

### Bottom Line

GAIA's security model is entirely application-layer today: a path allowlist, confirmation prompts for a known set of dangerous tools, and developer discipline. A misbehaving agent — or a malicious YAML manifest dropped into `~/.gaia/agents/` — runs with full user privileges. AXIS closes that at the kernel level.

The integration is a 4-phase project. Phase 1 (policy templates, no code changes) can start immediately. The critical load-bearing work is Phase 2 (out-of-process agents), which is a meaningful architectural change to GAIA's core chat pipeline. A realistic end-to-end timeline, assuming two teams working concurrently, is **16–22 weeks**. Phases can partially overlap but Phase 2 is a prerequisite for Phases 3 and 4.

---

## Code Review Findings

### AXIS — Strengths

- **Declarative policy as the primary interface.** All isolation behavior is configured via YAML. No Rust changes needed to support a new agent type.
- **No admin rights required.** AppContainer (Windows 11 Home), Landlock + seccomp (Linux, no root), Seatbelt (macOS) — all work without elevation.
- **OPA throughput.** The regorus-based network proxy evaluates 59,000 requests/sec at 17µs/request.
- **HIP Remote is genuinely unique.** No other local agent framework provides shared GPU access with per-tenant VRAM quotas and API filtering.
- **Structured audit.** OCSF events on a tokio broadcast channel — consumable by any WebSocket client with no additional instrumentation.

### AXIS — Gaps

| # | Gap | Impact |
|---|---|---|
| 1 | **`POST /api/v1/sandboxes` is a stub** — returns `501 Not Implemented`. The live path is `POST /api/v1/agents/:name/run`, which requires the agent to be pre-registered via `axis install`. | Blocks the cleanest Phase 2 implementation path. Must be resolved before integration work begins. |
| 2 | **`SandboxManager` uses a single global `Mutex`** — all create/destroy/list operations serialize. | Phase 3 (MCP sub-sandboxing) may spawn dozens of concurrent sandboxes. This is a **prerequisite architectural change** to AXIS before Phase 3 is viable, not a parallel concern. |
| 3 | **seccomp is x86_64 only** — hardcoded `AUDIT_ARCH_X86_64` check kills the process on any other arch. ARM64 gets no syscall filtering. | Low impact today (Ryzen AI is x86_64), but a portability debt. |
| 4 | **Inference server is shared across sandboxes** — the first sandbox that needs local inference starts `llama-server`; all subsequent sandboxes reuse it. | No per-sandbox inference isolation. Acceptable for now; relevant if multi-tenant scenarios emerge. |
| 5 | **`filesystem.deny` is parsed but not enforced** — the Landlock implementation only adds rules for `read_only` and `read_write`. `deny` entries are silently ignored. Landlock is already default-deny, so unlisted paths are blocked regardless — but the field is misleading to policy authors. | Documentation hazard; no security regression. |
| 6 | **No gateway authentication** — the REST API at `127.0.0.1:18519` has no auth and `Access-Control-Allow-Origin: *`. Security relies on the loopback constraint. | Low risk in current single-user desktop context. Needs addressing if GAIA's ngrok tunnel feature exposes it. |

---

### GAIA — Strengths

- **Clean layered architecture.** `Agent` ABC → domain agents → `AgentSDK` → `LLMClient` ABC → providers. Each layer is independently substitutable.
- **YAML manifest system.** Dynamic class construction via `type()` (not `eval`) is safe and supports no-code agent authoring.
- **`SSEOutputHandler` abstraction.** Agent code is completely transport-agnostic — the console interface maps cleanly to SSE events and is the natural seam for redirecting output to an AXIS PTY stream.
- **Tool confirmation protocol.** `threading.Event` + HTTP endpoint (`POST /api/chat/confirm-tool`) is a correct design for the async/sync boundary.
- **Privacy-first default.** Lemonade (local) is the default LLM provider. Claude/OpenAI require explicit opt-in.

### GAIA — Gaps

| # | Gap | Impact |
|---|---|---|
| 1 | **Global `_TOOL_REGISTRY` is a module-level dict shared across all agent instances.** `_register_tools()` clears and rebuilds it on every instantiation. YAML manifest agents call `_TOOL_REGISTRY.clear()` explicitly, which can wipe MCP tools registered by a concurrent agent. The `chat_semaphore = asyncio.Semaphore(1)` serializes chat requests but not agent discovery or `BuilderAgent` runtime creation. | Largest architectural risk in GAIA today. Phase 2 (out-of-process agents) eliminates it entirely. |
| 2 | **`TOOLS_REQUIRING_CONFIRMATION` is a hard-coded set of known tool names.** Any tool added by a YAML manifest that executes shell commands bypasses the confirmation gate. No mechanism for a tool to self-declare as requiring confirmation. | Real risk for third-party manifest agents. |
| 3 | **`PathValidator` is opt-in.** Instantiated at the `ChatAgent` level but each tool must explicitly call it. The `@tool` decorator and `_execute_tool()` dispatch layer do not enforce it. | Tools can silently bypass the path allowlist. |
| 4 | **Arbitrary Python loaded from `~/.gaia/agents/` with full process privileges.** `importlib.util.spec_from_file_location` has one guard: the path must be under `~/.gaia/agents/`. No code signing, no sandboxing of the loaded module. | High severity. Phase 2 (out-of-process agents) directly mitigates this — loaded code runs inside a sandboxed subprocess. |
| 5 | **MCP subprocesses inherit parent's full privileges.** `StdioTransport` uses bare `subprocess.Popen`. A malicious or buggy MCP server has unrestricted filesystem and network access. | High severity. Primary target of Phase 3. |
| 6 | **Only stdio MCP transport.** HTTP and SSE transport stubs raise `ValueError` at runtime. Remote MCP servers are not supported. | Medium impact. Unblocking HTTP transport is separate from AXIS integration but a natural follow-on. |
| 7 | **Unbounded SSE event queue.** `SSEOutputHandler` uses `queue.Queue()` with no size limit. On slow or disconnected clients, memory grows without bound. | Low probability in desktop context; worth fixing independently. |

---

## MVP — Proof of Life

Before committing to the full phased plan, a proof-of-life demo should validate the core premise: that AXIS can meaningfully isolate a GAIA agent session with no changes to GAIA's source code, and that the isolation is observable and verifiable.

**Target:** Working demo on a Linux dev machine in **1–2 days**.
**Team:** One engineer with an AI coding assistant (Claude Code or equivalent).
**GAIA changes required:** Zero.
**AXIS changes required:** Zero.

---

### What It Proves

1. A GAIA chat agent session runs correctly inside an AXIS sandbox (Lemonade inference works, tools execute, UI responds).
2. AXIS enforces a real policy boundary — an egress attempt to an unauthorized host is blocked and logged.
3. The OCSF audit stream captures the event — the block is observable, not just assumed.
4. Filesystem isolation holds — the sandboxed GAIA process cannot read or write outside its declared paths.

This is not a toy demo. Points 2–4 are the security properties that justify the integration. If any fail, the integration premise needs re-examination before further investment.

---

### Demo Scenario

The demo uses a single GAIA `ChatAgent` session with a tool that makes an outbound HTTP call. The AXIS policy permits only Lemonade (`localhost:8000`). The demo records what happens when the tool attempts to call an external endpoint (e.g., `api.openai.com`).

**Step 1 — Write the policy file**

```yaml
# ~/.axis/policies/gaia-mvp.yaml
version: 1
name: gaia-mvp

filesystem:
  read_only:
    - /usr
    - /lib
    - /lib64
    - /opt
    - ~/.gaia
    - ~/.cache/gaia
  read_write:
    - "{workspace}"
    - /tmp
  compatibility: best_effort

process:
  max_processes: 64
  max_memory_mb: 8192

network:
  mode: proxy
  policies:
    - name: lemonade-only
      endpoints:
        - host: localhost
          port: 8000
          access: read-write
          rules:
            - allow:
                method: POST
                path: /api/v1/chat/completions
            - allow:
                method: GET
                path: /api/v1/models

inference:
  routes:
    - name: lemonade
      endpoint: http://localhost:8000/api/v1
      provider: openai
      model: Qwen3.5-35B-A3B-GGUF
```

**Step 2 — Launch GAIA under AXIS**

```bash
axisd &
axis run --policy ~/.axis/policies/gaia-mvp.yaml -- \
  python -m gaia.ui.server --port 4200
```

**Step 3 — Run the demo sequence**

From the GAIA UI, send two chat messages to `ChatAgent`:

1. *"Summarize the README from this directory."* — uses the local RAG/file tool. Expected: works normally. Confirm in the audit log that only `localhost:8000` network activity is recorded.

2. *"Search the web for the latest AMD GPU benchmarks."* — triggers a tool that attempts an outbound HTTP call. Expected: AXIS proxy blocks it with `EPERM`. The agent receives a tool error and reports it. Confirm the block appears as an OCSF `NetworkAccessDenied` event in the audit stream.

**Step 4 — Verify filesystem isolation**

From inside the sandbox (via `axis exec <sandbox_id> -- bash`), attempt:
```bash
cat ~/.ssh/id_rsa       # should fail: EPERM (not in read_only list)
ls ~/Documents          # should fail: EPERM
echo "pwned" > ~/pwned  # should fail: EPERM
ls /tmp                 # should succeed: explicitly allowed
```

---

### Acceptance Criteria

| # | Criterion | How to Verify |
|---|---|---|
| 1 | GAIA chat UI loads and responds to a message via Lemonade | Browser — normal chat response |
| 2 | Local file tool executes successfully inside the sandbox | Chat — file summary returned without error |
| 3 | Outbound HTTP to an unauthorized host is blocked | Agent reports tool error; `axis logs <sandbox_id>` shows `NetworkAccessDenied` |
| 4 | Blocked event appears in OCSF audit stream | `wscat -c ws://localhost:18519/ws/v1/events` — event visible in real time |
| 5 | `~/.ssh/` is not readable from inside the sandbox | `axis exec` shell — `cat ~/.ssh/id_rsa` returns `EPERM` |
| 6 | Workspace path is writable | `axis exec` shell — temp file write succeeds in `/tmp` |
| 7 | GAIA runs normally without AXIS (no `axisd` running) | Kill `axisd`, restart GAIA directly — no errors |

Criterion 7 validates the degradation requirement before any code is written.

---

### What This MVP Does Not Prove

- Per-agent isolation (all agent sessions share one sandbox — this is a Phase 2 property)
- MCP subprocess sandboxing (Phase 3)
- GPU VRAM enforcement (Phase 4)
- Windows compatibility (Linux only for the MVP)

If all 7 criteria pass, proceed to the phased plan. If criterion 1 fails (GAIA does not work inside the sandbox), the policy needs tuning — likely missing a filesystem path in the Landlock rules. If criterion 3 fails (blocked call is not logged), the AXIS audit pipeline needs investigation before the integration is trustworthy.

---

## Integration Scope

### Assumptions

Estimates below assume:
- Two small teams (one familiar with GAIA Python, one with AXIS Rust), each using AI coding assistants (Claude Code or equivalent) throughout
- AI assistance is assumed for: boilerplate generation, test writing, policy YAML authoring, cross-language interface wiring, and documentation — not for architectural decisions or debugging novel integration failures
- Both teams have read access to both repos from day one
- CI/integration testing covers Linux and Windows (macOS is not a priority)
- AXIS remains an **optional dependency** of GAIA — GAIA must run normally when no AXIS endpoint is configured (see Degradation Strategy below)
- Phase 2 is not considered done until it ships a graceful fallback path

---

### Degradation Strategy (design requirement)

AXIS must be an optional enhancement, not a hard dependency. The integration must be controlled by a single environment variable, e.g. `AXIS_ENDPOINT=http://127.0.0.1:18519`. When unset:

- GAIA runs exactly as it does today (in-process agents, direct subprocess MCP)
- No AXIS-specific code paths execute
- No startup errors or warnings if `axisd` is not running

This is non-negotiable for adoption. GAIA has existing users and downstream dependents; a hard AXIS dependency would break them.

---

### Team Ownership

| Work | Owner | Dependency |
|---|---|---|
| Policy YAML templates | GAIA team | None |
| `gaia-agent-runner` subprocess script | GAIA team | None |
| `_chat_helpers.py` refactor | GAIA team | AXIS gateway `POST /api/v1/sandboxes` wired |
| `SSEOutputHandler.from_event_stream()` | GAIA team | None |
| Wire `POST /api/v1/sandboxes` to daemon | AXIS team | **Blocker for Phase 2** |
| `SandboxManager` mutex sharding | AXIS team | **Blocker for Phase 3** |
| `StdioTransport` → AXIS sandbox spawn | GAIA team | AXIS mutex sharding complete |
| HIP Remote benchmarking with PyTorch | AXIS team + GAIA team | Phase 2 complete |
| Integration CI (Linux + Windows) | Shared | Phase 2 complete |

---

### Phase 1 — Policy Templates
**Effort:** 2–3 days (with AI assistant) | **Teams:** GAIA | **Blockers:** None

**Goal:** Wrap any existing GAIA installation with AXIS today, with zero GAIA code changes.

**Work:**
- Write AXIS policy YAML templates for each GAIA agent type:
  - `gaia-chat.yaml` — filesystem read-only, Lemonade `localhost:8000` egress, no shell
  - `gaia-code.yaml` — workspace read-write, shell with npm/pip/git scoped, GitHub/PyPI HTTPS egress
  - `gaia-mcp.yaml` — base policy + per-MCP-server egress rules derived from `~/.gaia/mcp_servers.json`
  - `gaia-sd.yaml` — GPU enabled, VRAM limit, Hugging Face egress
- Add a `lemonade` inference route to each policy (`endpoint: http://localhost:8000/api/v1`, `provider: openai` — Lemonade is OpenAI-compatible)
- Document the launch invocation: `axis run --policy ~/.axis/policies/gaia-chat.yaml -- python -m gaia.ui.server`

**Key technical detail:** GAIA's `discover()` at startup calls `importlib.util.spec_from_file_location` on files in `~/.gaia/agents/`. Landlock rules must include `~/.gaia/agents/` as read-only and `~/.gaia/cache/` as read-write (for `PathValidator`'s `allowed_paths.json`).

**Limitation:** This wraps the entire GAIA server process in a single sandbox. All agent sessions share one isolation boundary. It is meaningfully better than no sandbox, but is not per-agent isolation.

---

### Phase 2 — Out-of-Process Agents via AXIS Gateway
**Effort:** 2–4 weeks (with AI assistant) | **Teams:** GAIA (primary), AXIS (stub fix) | **Blockers:** AXIS must wire `POST /api/v1/sandboxes`

**Goal:** Each GAIA agent session runs in its own AXIS sandbox. True per-agent OS isolation. Eliminates the global `_TOOL_REGISTRY` race condition. Contains arbitrary Python agent code loaded from `~/.gaia/agents/`.

**Architectural change:** GAIA's `Agent.process_query()` currently runs inside FastAPI's thread pool. AXIS can only sandbox a process it spawns. Agents must move out-of-process.

**Implementation:**

Introduce `gaia/agents/runner.py` — a thin subprocess entry point:
```
python -m gaia.agents.runner --agent-type chat --session <session-id>
```
This process loads the specified agent, reads the user request from stdin as JSON, runs `process_query()`, emits SSE event objects (the same types `SSEOutputHandler` already defines) to stdout as newline-delimited JSON, and exits.

In `gaia/ui/_chat_helpers.py`, replace the `_run_agent()` thread with:
1. `POST /api/v1/sandboxes` on the AXIS gateway → returns `sandbox_id`
2. `GET /ws/v1/sandboxes/<sandbox_id>/pty` → bidirectional PTY stream
3. Write the JSON request to the PTY stdin; read newline-delimited JSON events from PTY stdout
4. Feed parsed events into `SSEOutputHandler` as before

**GAIA code changes:**
- `gaia/ui/_chat_helpers.py` — replace `_run_agent()` thread with AXIS gateway call + PTY reader
- `gaia/ui/sse_handler.py` — add `from_event_stream()` factory that reads from a WebSocket/PTY instead of a thread-local queue
- `gaia/agents/runner.py` (new) — subprocess entry point, ~80 lines
- Policy YAML files per agent type (no Python code)

**AXIS change required (blocker):** Wire `POST /api/v1/sandboxes` to the daemon. Currently returns `501 Not Implemented`. Until this is done, the only path is `POST /api/v1/agents/:name/run`, which requires pre-registering GAIA agent types via `axis install gaia-chat gaia-code` — workable as an interim measure but not the right long-term interface.

**Degradation:** When `AXIS_ENDPOINT` is unset, `_chat_helpers.py` falls back to the current `_run_agent()` thread path. No behavior change for users without AXIS.

**Side effects (all positive):**
- `chat_semaphore = asyncio.Semaphore(1)` can be raised or removed — each subprocess has its own `_TOOL_REGISTRY`
- Agent crashes are fully contained; the FastAPI server does not go down
- Arbitrary Python from `~/.gaia/agents/*/agent.py` now runs inside a Landlock + seccomp sandbox

**Why 2–4 weeks with an AI assistant:** The mechanical parts of this phase — boilerplate for `runner.py`, the WebSocket reader in `sse_handler.py`, the AXIS gateway call in `_chat_helpers.py`, and the policy YAML files — are well-suited to AI-assisted generation given the clear interface contracts. The harder parts — debugging the PTY streaming protocol, handling disconnects and backpressure, cross-OS integration testing — still require human judgment and are where the 2 vs. 4 week variance lies. The AXIS gateway stub fix is a cross-team dependency that can be partially mitigated by using `POST /api/v1/agents/:name/run` as an interim path.

---

### Phase 3 — MCP Tool Sub-Sandboxing
**Effort:** 2–4 weeks GAIA side (with AI assistant); AXIS mutex refactor scoped separately | **Teams:** GAIA (primary), AXIS (mutex) | **Blockers:** AXIS `SandboxManager` mutex sharding must be resolved first

**Goal:** Each MCP server subprocess runs in its own AXIS sandbox, scoped to the network endpoints and filesystem paths it actually needs.

**Current state:** `StdioTransport.connect()` calls `subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)`. The MCP subprocess inherits the parent's full filesystem and network access. This is the highest-severity unfixed vulnerability in GAIA's current architecture — MCP tools are the primary attack surface for prompt injection leading to unrestricted subprocess execution.

**Implementation:** Replace `subprocess.Popen` in `StdioTransport` with an AXIS sandbox spawn. The MCP server continues to communicate over stdio (JSON-RPC over stdin/stdout) — AXIS's PTY passthrough carries this transparently.

```python
# gaia/mcp/client/transports/stdio.py — current
self._process = subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)

# With AXIS integration
sandbox_id = axis_client.create_sandbox(
    policy_yaml=_build_mcp_policy(server_config),
    command=cmd[0], args=cmd[1:]
)
self._pty_ws = axis_client.connect_pty(sandbox_id)
```

`_build_mcp_policy(server_config)` generates a minimal policy from the MCP server's entry in `mcp_servers.json`:
- `network.mode: proxy` with only that server's declared egress hosts
- `filesystem.read_only` derived from the server's declared tool paths
- `process.max_processes: 4`, `process.max_memory_mb: 512`

**AXIS prerequisite (hard blocker):** `SandboxManager` today uses a single `Arc<Mutex<SandboxManager>>`. A typical GAIA session connects to 3–10 MCP servers simultaneously (`MCPClientManager` connects in parallel with a `ThreadPoolExecutor`). Each connection spawns a sandbox. All of these serialize on the global mutex, and sandbox creation involves port allocation, GPU worker assignment, and process table updates. Sharding or segmenting the sandbox manager is a non-trivial Rust architectural change. **Phase 3 cannot be scoped or estimated properly until the AXIS team assesses the mutex refactor.** This should be a joint design session between teams before Phase 3 begins.

**Degradation:** When `AXIS_ENDPOINT` is unset, `StdioTransport` falls back to bare `subprocess.Popen`. Existing behavior unchanged.

---

### Phase 4 — GPU VRAM Quotas for SD/VLM Agents
**Effort:** 1 week benchmark spike + 1–2 weeks integration (with AI assistant) | **Teams:** AXIS + GAIA jointly | **Blockers:** Phase 2 complete; benchmark POC green

**Goal:** GAIA's SD (Stable Diffusion) and VLM (Qwen3-VL) agents get per-session VRAM limits.

**Implementation:** AXIS's HIP Remote layer (`libamdhip64.so`) is a drop-in that proxies all 538 HIP symbols over TCP to a `hip-worker` process. It is applied transparently via `LD_LIBRARY_PATH` injection when `gpu.enabled: true` in the policy — no GAIA code changes required.

```yaml
# gaia-sd.yaml
gpu:
  enabled: true
  device: 0
  vram_limit_mb: 8192
  compute_timeout_sec: 120
  denied_apis: [ipc_handles, device_reset, peer_access]
```

**Risk: GPU latency is unknown until benchmarked.** HIP Remote encodes every HIP API call — including tensor data transfers (weights, activations) — over TCP. For Stable Diffusion, GPU↔CPU data movement is a known throughput bottleneck. The overhead may be acceptable for inference workloads (which are already compute-dominated), but it may be significant for frequent small ops like embedding lookups in VLM. This **must be benchmarked before committing to Phase 4**. The AXIS benchmark suite (`benches/`) includes GPU throughput tests; a one-week POC spike should run them against a representative GAIA SD workload and report results before the full Phase 4 is approved.

If the latency overhead is unacceptable, a fallback is cgroup-based VRAM limits (Linux), which is less precise but has no per-call overhead.

---

## Summary

### Effort, Ownership, and Value Matrix

| Phase | Effort (with AI assistant) | GAIA Changes | AXIS Changes | Blocker | Security Value | AMD Platform Value |
|---|---|---|---|---|---|---|
| MVP — Proof of life | 1–2 days | None | None | None | Validates premise | Validates premise |
| 1 — Policy templates | 2–3 days | None | None | None | Medium (process-level) | Low–Medium |
| 2 — Out-of-process agents | 2–4 weeks | `_chat_helpers.py`, `sse_handler.py`, `runner.py` | Wire `POST /api/v1/sandboxes` | AXIS stub fix | High (per-agent OS isolation) | High |
| 3 — MCP sub-sandboxing | 2–4 weeks (GAIA side) | `StdioTransport`, policy generator | `SandboxManager` sharding | AXIS mutex refactor | Very High (tool-level isolation) | High |
| 4 — GPU VRAM quotas | 1wk spike + 1–2 weeks | None | Benchmarking, integration CI | Phase 2 done; benchmark POC | Unknown until benchmarked | Very High (AMD differentiator) |

### Critical Path

Phase 2 is load-bearing. It:
- Delivers true per-agent OS isolation
- Eliminates the `_TOOL_REGISTRY` race condition
- Sandboxes arbitrary Python loaded from `~/.gaia/agents/`
- Provides the subprocess boundary that Phases 3 and 4 require

**Minimum calendar time with full parallelism and AI coding assistants:** ~6–9 weeks. MVP and Phase 1 run in parallel in the first week. Phase 2 begins immediately after, gated on the AXIS stub fix. Phase 3 GAIA-side work runs in parallel with Phase 2's tail; the AXIS mutex refactor is scoped concurrently. Phase 4 spike starts once Phase 2 ships. The primary schedule driver is the two cross-team AXIS blockers (stub fix, mutex refactor), not the GAIA implementation effort.

### Immediate Recommendations

1. **Start Phase 1 now** — zero code changes, tangible security improvement, validates the operational model.
2. **AXIS team: wire `POST /api/v1/sandboxes`** — this is the single most important AXIS-side action to unblock Phase 2.
3. **Joint design session on `SandboxManager` mutex** — before Phase 3 is formally scoped, the AXIS team needs to assess the refactor cost. If it's large, Phase 3 may need to be re-sequenced.
4. **Run the GPU benchmark POC before approving Phase 4** — HIP Remote latency with PyTorch is the only material unknown in this integration. Answer it cheaply before committing.

---

## Appendix — Repository Profiles

### AXIS (axis-sandbox/axis)

| Property | Detail |
|---|---|
| Language | Rust (85%), TypeScript/Swift/C# (GUI), Bash/PowerShell (E2E) |
| Version | 0.3.5 |
| License | Apache-2.0 |
| Gateway port | 18519 (localhost only) |
| Sandbox startup | 0.6ms (Linux), 1.6ms (Windows) |
| Memory per sandbox | ~1.6MB |
| Isolation primitives | Landlock LSM v2+, seccomp-BPF (142-syscall whitelist), Linux netns/veth, AppContainer + Job Objects (Windows), Seatbelt (macOS) |
| Policy engine | regorus (pure-Rust OPA/Rego) |
| GPU isolation | HIP Remote — 538-symbol drop-in `libamdhip64.so` over TCP |
| Installed agents | claude-code, codex, aider, goose, gemini-cli, opencode |

**Crate structure:**

| Crate | Role |
|---|---|
| `axis-core` | Policy schema (YAML→Rust), OPA engine (regorus), OCSF audit types |
| `axis-sandbox` | OS isolation: Landlock + seccomp + netns (Linux); AppContainer + Job Objects + ConPTY (Windows); Seatbelt (macOS) |
| `axis-proxy` | Per-sandbox HTTP CONNECT proxy — OPA eval per request, TLS termination, credential injection |
| `axis-router` | Inference routing — DRR scheduler, 8-dimension smart routing, token budgets, model registry |
| `axis-gpu` | HIP Remote para-virtual GPU — protocol codec, API filter, VRAM quota, worker lifecycle |
| `axis-pty` | PTY session management — Unix PTY and Windows ConPTY |
| `axis-gateway` | HTTP + WebSocket API server for GUI/orchestrator clients |
| `axis-daemon` | `axisd` — sandbox lifecycle manager, policy hot-reload, Unix socket / TCP IPC |
| `axis-cli` | `axis` CLI — run/create/exec/destroy/list/install/policy/model |
| `axis-safety` | Credential leak detection (Aho-Corasick, 11 pattern categories) |

---

### GAIA (amd/gaia)

| Property | Detail |
|---|---|
| Language | Python 3.10–3.12 (84%), TypeScript/React (Electron UI) |
| Version | 0.17.3 (`amd-gaia` on PyPI) |
| License | MIT |
| UI port | 4200 (FastAPI + optional Electron shell) |
| LLM default | Lemonade Server (OpenAI-compatible, `localhost:8000`) |
| LLM providers | Lemonade (AMD), OpenAI, Claude (Anthropic) |
| MCP transport | stdio only (HTTP/SSE stubs raise `ValueError`) |
| Agent execution | In-process; `Agent.process_query()` runs in FastAPI thread pool |
| Concurrency | `asyncio.Semaphore(1)` — one chat request at a time (global `_TOOL_REGISTRY` constraint) |
| Plugin discovery | `~/.gaia/agents/*/agent.py` or `agent.yaml` (Pydantic `AgentManifest`) |

**Key packages:**

| Package | Role |
|---|---|
| `gaia.agents.base` | `Agent` ABC, `@tool` decorator, global `_TOOL_REGISTRY`, `MCPAgent` |
| `gaia.agents.registry` | `AgentRegistry`, `AgentManifest` (Pydantic v2), dynamic class creation via `type()` |
| `gaia.ui.server` | FastAPI app factory, CORS + tunnel auth middleware, SSE streaming |
| `gaia.ui.sse_handler` | `SSEOutputHandler` — agent console events → typed SSE JSON |
| `gaia.ui._chat_helpers` | Agent creation, session caching, streaming pipeline |
| `gaia.mcp.client` | `MCPClient`, `MCPClientManager`, `StdioTransport` (subprocess JSON-RPC) |
| `gaia.mcp.mixin` | `MCPClientMixin` — registers MCP tools into `_TOOL_REGISTRY` at connect time |
| `gaia.llm.factory` | `create_client()` — lemonade / openai / claude |
| `gaia.security` | `PathValidator` (allowlist, resolves symlinks), `TOOLS_REQUIRING_CONFIRMATION` |
