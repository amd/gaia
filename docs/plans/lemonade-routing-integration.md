# Scope: model tiering for enterprise cost reduction

**Goal:** cut enterprise inference spend by running each task on the cheapest
model that can do it — code generation on Opus 5 via a cloud gateway, email
triage on an on-prem Gemma, and so on.

**Recommendation: put the tiering decision in GAIA at the *skill* boundary, not
in Lemonade's router at the *request* boundary. Use Lemonade's router as the
policy backstop, not the cost optimizer.**

The architecture this needs is already designed —
[`skill-bound-task-execution.md`](skill-bound-task-execution.md) — written for
device placement (NPU vs GPU). Extending it from *device* tiering to *cost*
tiering is mostly additive.

Note what this implies: **a tiering design built this way barely uses the router.**
Once a skill resolves to a tier, GAIA addresses that model by name — including a
cloud model registered in Lemonade (`vercel.<opus-id>`). The router earns its
place only as the guardrail in §1, and the cost ledger GAIA has to own regardless
(§3.4).

---

## 1. Why the router is the wrong place for this decision

Lemonade's router decides from **request content**: `keywords_any`, `regex`,
`min_chars` / `max_total_chars`, `has_tools`, `has_images`, plus optional
model-backed classifiers. It is a content sniffer.

But "this is a code-generation task" is not a property of the message text. It is
a property of *what the user asked the system to do* — which **GAIA knows exactly
and Lemonade cannot see**. A rule like `keywords_any: ["def ", "function"]` is a
lossy proxy for something already known for certain upstream.

The failure is not hypothetical and it runs the wrong way for a cost goal:

> *"Summarize this email thread about our new function definitions."*

Hits the code-gen keyword rule. Routes an email summary to Opus. **Spend goes up,
and the chargeback is attributed to the wrong task.** For enterprise cost control
the property you need is *predictability* — a finance owner has to be able to
answer "why did this cost that?" A keyword match on user-authored prose cannot
answer it.

Three further mismatches, all confirmed upstream:

- **Cost is structurally incapable of affecting selection.** `CostInfo` in
  [`routing_policy.h`](https://github.com/lemonade-sdk/lemonade/blob/main/src/cpp/include/lemon/routing_policy.h)
  is documented as *"looked up **after** `route_to` is resolved"* and surfaced as
  *"illustrative `estimated_cost` — not a billing figure."* This is architecture,
  not an oversight. There is no budget cap, no cheapest-that-qualifies rule, no
  spend ceiling, and no per-model quota —
  [#3078](https://github.com/lemonade-sdk/lemonade/issues/3078) requesting them is
  open. Prices are scraped at discovery from **OpenRouter and Together only**;
  everything else reports `-1.0` (unknown). A router cannot optimize for cost.
- **No capacity-aware filtering** ([#2959](https://github.com/lemonade-sdk/lemonade/issues/2959), open) — the router will not check the request fits the candidate's window.
- **Route flapping** ([#2956](https://github.com/lemonade-sdk/lemonade/issues/2956), open) — no hysteresis, so a single conversation can bounce tiers turn to turn. Cost becomes non-reproducible across identical sessions.

### Where the router *is* the right tool

As a **guardrail**, not an optimizer. A server-side policy holds even for requests
GAIA did not construct — a raw `curl`, a third-party tool, a misconfigured agent:

- `metadata: {consent: denied}` → force the on-prem candidate. Data residency
  becomes server-enforced rather than an application convention.
- A per-tenant ceiling: this tenant may never reach a paid tier.

That is genuinely better in Lemonade than in GAIA, because it holds where GAIA's
own checks cannot reach. It is small, high-value, and the only part of the
"integrate the router" framing worth keeping.

**The split:**

| Layer | Decides | Property it provides |
|---|---|---|
| GAIA skill-bound task runner | Which tier a task runs on | Deterministic, task-aware, attributable, unit-testable |
| Lemonade router | What a request may *never* do | Server-side, unbypassable, provider-agnostic |

---

## 2. Reaching a cloud tier: two paths, and the cheap one is not obvious

GAIA has **no tool-calling path to a cloud model except Anthropic direct**:

| Provider | Lines | Tool calls | Custom `base_url` |
|---|---|---|---|
| `providers/lemonade.py` | 597 | **yes** | yes |
| `providers/claude.py` | 518 | **yes** (`NATIVE_TOOL_CALLS_PREFIX`) | n/a |
| `providers/openai_provider.py` | 79 | **no** | **no** — hardcodes `openai.OpenAI(api_key=...)` |
| `providers/litellm.py` | 95 | **no** — returns `.choices[0].message.content` only | via litellm |

Code generation is the *most* tool-heavy workload GAIA has (`hub/skills/coding`
declares `read_file`, `edit_file`, `search_code_index`, `execute_python_file`).
Running it on Opus requires native `tool_calls` plus a custom `base_url` and auth
header. Neither stub qualifies, and `OpenAIProvider` cannot even be pointed at a
gateway.

**But GAIA may not need a new provider at all.** Lemonade's cloud adapter is
generic — an arbitrary provider name plus a URL, with no allowlist. The entire
per-provider config surface, from
[`cloud_provider_registry.h`](https://github.com/lemonade-sdk/lemonade/blob/main/src/cpp/include/lemon/cloud_provider_registry.h):

```cpp
std::string name;                  // arbitrary — docs' own example uses "acme"
std::string base_url;
std::string auth_header_name = "Authorization";
std::string auth_header_prefix = "Bearer ";
std::string wire_format = "openai";
```

Vercel AI Gateway matches the defaults exactly — `https://ai-gateway.vercel.sh/v1`,
`Authorization: Bearer`, OpenAI-format `/v1/models`. So it should register with no
custom flags:

```bash
lemonade cloud install vercel --base-url https://ai-gateway.vercel.sh/v1
```

| Path | GAIA change | Trade-off |
|---|---|---|
| **A. Gateway behind Lemonade** | **None** — existing `lemonade.py` provider already speaks tool calls | One client path; AMD's server sits in the paid-token path |
| **B. Gateway beside Lemonade** | Finish `litellm.py` to tool-calling parity, or add a generic OpenAI-compatible provider | GAIA owns the cloud call; a second code path to the same model |

**Try A first** — it is a config change against code that already works, so it
costs an afternoon to falsify. Two things to verify before committing to it,
because neither is confirmed upstream: that a **tool-calling** turn round-trips
cleanly through Lemonade → Vercel → Opus, and that the router does not fall a
tool-heavy request open to a `default_model` that cannot take tools (no candidate
capability enforcement was found).

Operational notes for A, all enterprise-relevant: keys are *"never written to disk
by `lemond`"*; `/v1/system-info` reports auth status but never the key; and an
env-var "house key" cannot be overridden by a client (`409 auth_conflict`) — a
sound operator model for a shared server. **Use a long-lived AI Gateway API key,
not a Vercel OIDC token** — Lemonade supports exactly one auth header with no
refresh mechanism, and OIDC tokens are short-lived.

---

## 3. The architecture already exists

[`skill-bound-task-execution.md`](skill-bound-task-execution.md) designs exactly
the "multiple models across task boundaries" shape, and its reasoning transfers
intact. Its core move: promote `DeviceConfig`
(`src/gaia/agents/registry.py:283`) from machine-scoped to **task-scoped**, and
let a skill declare what it needs.

```yaml
metadata:
  gaia:
    runtime:
      requires:
        class: small-fast       # small-fast | general | reasoning | vision
        ctx: 8192
        tools: true
      prefer_device: npu
      pin: gemma4-it-e2b-FLM    # escape hatch, no substitution
```

Its own open question already anticipates this conversation: *"Where do
remote/cluster targets appear? Cleanest is a `DeviceConfig` whose device is a
cluster queue, so the resolver is unchanged and only the runner differs."*

**Cost tiering is that extension.** What it adds:

1. **A `remote` target class** in the resolver — `DeviceConfig.device` gains a
   remote/gateway variant carrying provider, `base_url`, and model id. The
   resolver logic is unchanged; only the runner differs.
2. **A price dimension** on the tier. `ModelTier` (`registry.py:364`) is the
   existing precedent for "a capability declares a model preference list" — it
   already carries `min_memory_gb`. Add cost-per-Mtok in/out and let the resolver
   pick the cheapest tier satisfying `requires`.
3. **Budget admission.** The plan's admission checks run at *submit* and are all
   hard rejects. A spend check belongs in exactly that list: this task, at this
   tier, against this team's remaining budget. Rejecting at submit is what makes
   spend predictable — the property Lemonade's router cannot give you.
4. **Attribution.** Every task already knows its skill; every turn already records
   its model. Emit `(skill, model, tier, tokens, cost)`.

   Lemonade will not do this for you. Its routing counters are bare scalars with
   no label dimension (`routing_decisions_total_`, `routing_switches_total_` in
   `router.h`), and nothing in request `metadata` reaches Prometheus labels. The
   one viable server-side path is **OpenTelemetry**: OTLP spans carry `session.id`
   and `gen_ai.conversation.id`, resolvable from configurable headers —

   ```bash
   lemonade config set telemetry.session.headers.id="x-corp-session"
   lemonade config set telemetry.session.headers.client="x-corp-client"
   ```

   — and include token counts and model names. So the raw material for chargeback
   exists, but the token-count → price join is yours to build. That is the
   ledger, and it belongs in GAIA next to the skill identity anyway.

Everything else in that plan — the multi-slot broker, the async task contract,
the artifact store — is required regardless and is already sequenced there.

### Two constraints from that plan that bite harder here

- **The 180s synchronous tool-call cap** (`DEFAULT_TOOL_TIMEOUT`, `agent.py:140`).
  Described there as "small and absolute" — until a tool can return a handle
  instead of a payload, none of this runs. A cloud reasoning model on a hard task
  will exceed it.
- **`tool_calling=False` is a hard admission reject.** `gemma4-it-e2b-FLM`
  500-errors on an OpenAI `tools` payload (verified on hardware,
  `lemonade_client.py:349-360`). The cheapest tier is often the one that cannot
  take tools — so "route the simple task to the cheap model" silently means
  "route it to a model that cannot call tools" unless admission catches it.

---

## 4. On sub-agents: the earlier "no" was premise-dependent, and the premise changed

Sub-agent orchestration has been rejected twice — [#674](https://github.com/amd/gaia/issues/674)
closed 2026-09-02 ("not the architecture"), and
[`subagent-context-isolation.md`](subagent-context-isolation.md) superseded. That
history is worth respecting, but one of its load-bearing premises does not hold
in the enterprise setting. Verbatim from the superseded doc:

> **One resident `(model, ctx)` pair.** The child runs the same Gemma-4-E4B. No
> "cheap model for grunt work" — Claude Code's single biggest lever is
> unavailable.

With a cloud tier plus an on-prem server, **that lever is available.** Cloud
candidates consume no local residency slot at all, and `max_loaded_models` is a
config default rather than a hardware limit (the correction that doc itself
carries at the top). So the intuition that multi-model across task boundaries now
makes sense is right, and it is right *because the enterprise premise changes the
input to the earlier decision.*

**But the mechanism should still not be free-text delegation.** The superseding
doc's argument against it is premise-independent and still stands: delegation
requires the parent model to write a good cold-start prompt for a child that sees
no conversation, and its quality is capped by that. A skill-bound task takes
**typed arguments into a human-authored procedure** — nobody generates a prompt,
so the objection never applies.

For cost specifically, skill-bound tasks win on a second axis: a task keyed to a
reviewed `SKILL.md` has *predictable* spend, because the procedure is fixed. A
delegated sub-agent improvises its own tool loop, so its token cost is bounded
only by the step cap. Enterprise budgeting needs the former.

So: **multi-model across task boundaries — yes. Sub-agent delegation as the
mechanism — still no.** Skill-bound tasks give the same model-per-task binding
with bounded, attributable cost.

---

## 5. Build order

**Step 0 — Measure. Do this first regardless of everything above.**
You cannot claim a cost reduction without a baseline, and nothing in GAIA prices
a token today. The accounting is already there and unused:
`turn_metrics.py` records per-turn `model`, `input_tokens_server`,
`output_tokens_server`, and cache read/creation tokens (lines 299-333). What is
missing is a price table and the skill dimension. Adding
`(skill, model, tokens, $)` to the existing stats surface is small, useful on its
own, and produces the business case that justifies steps 1-5.

1. **Prove a cloud tier reaches Opus with tools intact.** Register Vercel as a
   Lemonade cloud provider (path A, §2) and run one tool-calling turn end to end.
   A day's work against existing code. If it round-trips, the cloud tier needs no
   GAIA provider work at all; if it doesn't, fall back to path B and finish
   `litellm.py`. Falsify this before building anything on top of it.
2. **Async task contract + task store** — the unblocking primitive for the 180s cap.
3. **`runtime` frontmatter + resolver + admission** — pure logic, unit-testable
   with no hardware. Add the cost/budget check to the admission list here.
4. **Multi-slot, memory-budgeted broker** — raise `max_loaded_models`, place by
   resolved config. Confirmed supported: `max_loaded_models=N`, `-1` for
   unlimited, per-`ModelType` LRUs. On a GPU serving box the NPU exclusivity rules
   that dominate the laptop case do not apply, so co-resident local candidates are
   genuinely free.
5. **Lemonade router as the policy backstop** — the consent/tier-ceiling policy
   from §1. Independent of 1-4; needs a Lemonade bump to ≥ v11.7.0 (this box runs
   v11.5.0; `/routing/validate` 404s, there are no routing counters in
   `/v1/stats`, and the cloud `--wire-format` / `--auth-header-name` flags
   postdate it too).

Steps 0 and 1 are worth doing even if the rest is never built.

---

## 6. Test plan

- [ ] **Cost accounting**: unit test that a turn on a priced model emits the
      expected `(skill, model, tokens, $)` record; golden-file the price table.
- [ ] **Gateway provider**: integration test that a tool-calling turn against a
      real OpenAI-compatible gateway round-trips `tool_calls` — mocked HTTP proves
      only that we called it, never that the call is valid (CLAUDE.md).
- [ ] **Admission rejects**, each a hard fail with an actionable message: a
      tools-required skill resolving to `tool_calling=False`; `requires.ctx` above
      the device ceiling; a `pin`ned model not installed; a task over budget.
- [ ] **Cold-state**: resolve and run a skill on a box where the tier's model was
      never pulled (#1655 class — a warm cache hides this).
- [ ] **Determinism**: the same skill + inputs resolves to the same tier across
      runs. This is the property the router cannot provide and the reason for the
      design; pin it.
- [ ] **Agent eval before/after** on any skill whose tier changes — changing which
      model answers is an LLM-affecting surface by definition. Serially.

---

## 7. Open questions

1. **Does a tool-calling turn survive Lemonade → Vercel → Opus?** The single
   question that picks path A or B in §2, and it is cheap to answer. Also
   unconfirmed: whether the router enforces that a chosen candidate can take
   tools, and what model id Vercel discovery produces (ids are
   `<provider>.<cleaned_upstream_id>`; Vercel's `creator/model` shape is
   different — run `lemonade list | grep vercel`, don't guess it into a policy).
2. **Which on-prem model?** Lemonade's built-in catalog ships
   `Gemma-4-31B-it-GGUF`, `Gemma-4-26B-A4B-it-GGUF`, and `Gemma-4-12B-it-GGUF`;
   none are pulled on this box, and GAIA's own `MODELS` registry knows only
   E2B/E4B and `Qwen3.5-35B-A3B-GGUF`. For triage specifically the **26B-A4B MoE**
   activates ~3.8B params per token and should beat the 31B dense on
   throughput at similar quality — and `Qwen3.6-35B-A3B-FP16-vLLM` is the
   throughput-oriented serving build. Any of these needs a `MODELS` entry with its
   `tool_calling` flag and `min_ctx_size` before GAIA can target it.
3. **Where does the budget live** — per user, per team, per skill? Determines
   whether admission needs an external budget service or a local counter.
4. **Does a task inherit the caller's permissions and budget?** The skill declares
   `permissions`; the caller has grants. Deny-only inheritance is the safe default
   for both.
5. **What is the cost of a *wrong* tier?** Routing a hard task to the cheap model
   costs a failed turn plus a retry on the expensive one — which can exceed
   going expensive first. Needs measuring before the resolver's defaults are set.

---

## 8. Relationship to existing work

- [`skill-bound-task-execution.md`](skill-bound-task-execution.md) — the parent
  design. This document is the cost/remote extension of it, not a replacement.
- [`subagent-context-isolation.md`](subagent-context-isolation.md) — superseded;
  §4 above revisits one of its premises.
- [#1000](https://github.com/amd/gaia/issues/1000) — multi-model parallelism.
  Same motivation, client-side role routing. Should probably be folded into the
  skill-bound design rather than built separately.
- [`gaia-agent-latency.md`](gaia-agent-latency.md) — tiering does not help prompt
  size, the measured latency dominator. Do not sell this as a latency win.
