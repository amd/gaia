# Flagship Agent — Architecture Findings from the Eval Build

Findings and recommendations from building and running the flagship `gaia` agent's
first end-to-end evaluation (Aug 2026). Every recommendation below is grounded in a
defect this work actually produced, not in general principle.

## Evidence base — and its limits

55 of 99 scenarios executed against the live agent through the Go TUI, agent on
`claude-haiku-4-5`, plus the deterministic tier in hosted CI. Five real defects
found; four fixed in ~95 lines of product code.

**Read the low fix-cost carefully.** Every defect sat at a *seam* — provider
adapter, transport, dispatcher, security guard — and seam defects are translation
errors, which are cheap to repair. It is not evidence that the core is proven. The
44 unrun scenarios are the *hardest* subsystems (memory v2, RAG, code index,
dynamic tool selection); they need the Lemonade embedder and have no behavioural
evidence either way yet.

| Defect | Layer | Fix size |
|---|---|---|
| `/clear` left the child's `conversation_history` intact | transport (stdio) | 31 lines |
| Skill-namespaced tool names 400'd the Anthropic API | provider adapter | 48 lines |
| Hyphenated skill tools undispatchable ("Unknown tool name") | dispatcher | 12 lines |
| Web fetch refused its own loopback fixtures | security guard | 17 lines |
| Shell tool reads outside the `allowed_paths` sandbox | tool layer | *open* |

## The through-line

> **Invariants live in convention and at call sites, rather than in chokepoints.**

Four of the five defects are the same disease. The codebase already contains the
cure, applied once and well: `refuse_unbridged_permissions`
(`src/gaia/skills/permissions.py:192`) is a single function that install, publish,
migrate, `register_skill_tools`, and `Agent.load_skill` all funnel through. One
place to reason about, impossible to forget. Nothing else enforces its invariant
that way.

The tell is written into the test suite. Several refusal tests in
`hub/agents/gaia/python/tests/test_skill_library_tools.py` ship with a companion
test asserting *"the substrate itself does not gate this"* — e.g.
`test_the_substrate_deletes_outside_the_skills_root`. The safety lives in the
wrapper; the primitive underneath is sharp. That is a correct description of the
current design and an accurate prediction of where the next bug lands: the next
caller that forgets a guard.

---

## Recommendations

### 1. One path authority for every filesystem-touching tool

**Evidence.** `read_file` validates against `allowed_paths`
(`file_io_tools.py:38`). `run_shell_command` does not, so `type
C:\Windows\System32\drivers\etc\hosts` walks straight out of the home sandbox
under `--bypass-permissions`. Two tools, two policies; the weaker one defines the
real boundary.

**Recommendation.** A `PathPolicy` chokepoint that every file-touching surface
resolves through — `read_file`, `write_file`/`edit_file`, `analyze_data_file`,
code-index roots, RAG ingestion, and **shell command arguments**. Shell is the
hard case (arguments are opaque strings), so scope it honestly: parse argv,
resolve path-like tokens, refuse out-of-sandbox ones — the mechanism
`shell_tools.py:886` already applies for `resolve-path` arguments, generalised and
made mandatory rather than opt-in per tool.

**Test shape.** One parametrised suite over *all* file-touching tools asserting an
identical refusal for the same out-of-sandbox path. A new tool joins the list or
fails the suite.

### 2. Make session-scoped state a first-class concept

**Evidence.** Three leaks of the same class in a single session: `/clear` didn't
clear agent history; installed skills persisted to `~/.gaia/skills` and leaked
into later scenarios; the scratchpad DB persisted to `~/.gaia/scratchpad.db`, so
re-loading a CSV stacked duplicate rows and inflated every filtered aggregate
(totals and ratios stayed correct — which is exactly why it went unnoticed).

**Recommendation.** An explicit lifecycle contract: state declares its scope
(`turn` / `session` / `durable`) and the agent exposes one `reset(scope)` that all
subsystems implement — history, scratchpad tables, loaded skills, per-session
indexes, tool grants. `/clear`, a new eval scenario, and a returning user are then
the same code path.

**Product consequence beyond tests.** Nothing today stops a *user's* second
analysis from double-counting the first load. This is a real correctness bug, not
only a harness artifact.

### 3. Give the provider abstraction a real contract

**Evidence.** Skill tools register as `<skill>/<tool>`. Lemonade accepted the `/`;
Anthropic rejects it (`^[a-zA-Z0-9_-]{1,128}$`), so loading any tool-bearing skill
400'd **every subsequent turn** — the entire skill system was unusable on Claude
and nothing caught it until an eval ran on a non-default provider. Separately, the
eval MCP bridge hardcoded `"model": DEFAULT_MODEL_NAME`, silently defeating a
provider override.

**Recommendation.** `LLMClient` declares its constraints (tool-name pattern, tool
count/schema limits, cache semantics, streaming stats availability), and
normalisation happens **once at the boundary** with the inverse mapping applied to
responses — the shape now implemented in `providers/claude.py`, but as an
interface obligation rather than one provider's private fix. Add a **provider
conformance suite** every provider must pass: register a hostile-but-legal tool
name, a large tool set, a multi-tool turn; assert identical observable behaviour.

### 4. Enforce cross-transport conformance mechanically

**Evidence.** `TranscriptResetter` was implemented by the SSE transport and not by
the subprocess transport, with nothing to catch the gap — so `/clear` was
correct on one transport and broken on another. Prior art in the repo: the
capability ladder ran green for a day while the TUI had *no conversation history
at all*, because every rung was self-contained and the HTTP-layer tests passed.

**Recommendation.** One shared conformance suite every transport must pass —
history accumulation, `/clear`, cancellation, confirmation semantics, exactly-one
terminal event — plus compile-time assertions where the language offers them
(`var _ TranscriptResetter = (*SubprocessClient)(nil)`, added by this work, is the
pattern). Transport-specific tests then cover only genuinely transport-specific
behaviour.

### 5. Move guards into the substrate; keep the sharp primitive private

**Evidence.** `install.remove_skill` will `rmtree` an unvalidated path; the base
loader has no tier gate. Safety is added by the four refusals in
`skill_library_tools.py` — correct today, fragile for every future caller.

**Recommendation.** Validation belongs in the substrate (`SkillManager`,
`install`), with the unvalidated primitive private. Wrappers then add *policy*
(who may call, with confirmation) rather than *safety*. Delete the "the substrate
does not gate this" companion tests when they become false — their existence is
the design smell.

**Directly relevant now.** The skill-capture feature adds a trust boundary
(untrusted instructions into the system prompt; untrusted code on disk). That is
precisely when the chokepoint should be built, rather than a fifth call-site
guard.

### 6. Design test affordances into security controls

**Evidence.** The SSRF guard blocked `127.0.0.1` — correct — but had no
sanctioned way to reach a loopback fixture server, so `gaia_web` was untestable
end-to-end (0/4) until `GAIA_WEB_ALLOWED_HOSTS` was added (default-off,
host-scoped, both check points honouring it; 4/4 after).

**Recommendation.** Treat "how is this verified end-to-end?" as part of a security
control's design. Every control ships a documented, default-off, narrowly-scoped
affordance. A control with no test path is a control that silently rots — and one
that tempts a future engineer into a far broader bypass.

### 7. Observability is a contract, not a byproduct

**Evidence.** `agent_ui_mcp._stream_chat` captures per-turn `stats`
(`time_to_first_token`, `tokens_per_second`, `input_tokens`, `output_tokens`), and
`runner.py` drops them — so the eval could not report the very KPIs needed to
judge prompt efficiency without a code change.

**Recommendation.** Per-turn stats are part of the turn contract, propagated
unmodified from provider → transport → runner → scorecard, with the *source*
recorded (Lemonade counters vs Anthropic `usage`) so unlike measurements are never
conflated. A missing counter reports `not measured` — never an estimate.

### 8. Order prompt assembly by mutation rate

**Evidence / open question.** The flagship uses dynamic tool selection
(`dynamic_tools_max=26`) and lazy skill-body activation to cut per-turn tokens
(~10.2K → ~4.2K for `tools=`). But both mutate the prompt's *middle*, and
llama.cpp prefix caching rewards a byte-stable *prefix*. Fewer tokens sent can
mean more tokens re-processed.

**Recommendation.** Assemble prompts in mutation-rate order — stable system
identity and CORE tools first, per-turn selected tools and query last — so the
cacheable prefix is maximal by construction, with explicit cache breakpoints at
the boundary. Then verify with the cache-hit-ratio KPI on the self-hosted runner
per model; this is a claim to measure, not to assume.

---

## What is working, and should be extended rather than replaced

- **Mixin composition.** 67 tools across 18 cohesion bundles, composed by name via
  `KNOWN_TOOLS`, with no base-class modification per capability. This scaled well.
- **Defense in depth where it was applied.** The SSRF guard validates twice
  (pre-flight DNS *and* the pinned IP actually dialed) specifically to close a
  rebind window. `read_file` held its sandbox even when shell did not.
- **The trust model for skills.** Tier × signature × permission ceiling × audit is
  a genuinely good design — the unsigned-skill refusal held under direct user
  pressure in evaluation. Generalise it to *every* untrusted artifact (captured
  skills, MCP servers, connectors), rather than rebuilding per surface.
- **Fail-loudly discipline.** Actionable errors naming what failed, what to do, and
  where to look are the norm, and they materially shortened debugging here.
- **The audit engine.** AST-based sink analysis plus instruction-injection scanning
  that never executes code or touches the network — reusable at runtime, not only
  at publish time.

## Suggested order

1. **§2 session-scoped state** — a live correctness bug (user-facing double-count),
   and it unblocks reliable multi-scenario evaluation.
2. **§1 path authority** — an open security finding.
3. **§4 transport conformance** + **§3 provider contract** — cheap, and each
   retires a whole bug class rather than a bug.
4. **§5 substrate guards** — do it *with* the capture feature, while the trust
   boundary is being drawn.
5. **§7 observability** then **§8 prompt ordering** — measure before tuning.

§6 is a review-checklist item, not a project.

## Caveat

These recommendations rest on the subsystems that were actually exercised.
Memory v2, RAG, and the code index — the three most complex — were not, because
they require the embedder. The first self-hosted runner pass should be treated as
capable of *adding* findings here, not confirming this list.
