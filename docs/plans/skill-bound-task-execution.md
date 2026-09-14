# Skill-bound task execution

**Status:** architecture design. Nothing built.
**The requirement:** run an independent task, on a skill, bound to a specific backend/model.

**Why a work plane rather than sub-agent delegation.** Agent delegation scales *context*; it does not scale *compute*. A sub-agent is a second LLM conversation — it has no resource requirement, no queue, no retry, no cancel, no resume, and no way to outlive the session that spawned it, so pointing one at a GPU does not give it those properties. When the bottleneck is device-seconds rather than window size, the right shape is typed, resource-tagged, asynchronous jobs that the agent *submits and monitors* rather than *becomes*. Placement is then a scheduler decision, not an agent decision — which is why the binding below lives on the skill and the resolver, never in an agent definition.

## The shape

**`DeviceConfig` is already the binding you want.** It exists today at `src/gaia/agents/registry.py:283` and carries exactly the fields the requirement names:

```python
device: Literal["cpu", "gpu", "npu"]
model: str        # "gemma4-it-e2b-FLM"
recipe: str       # "flm" | "llamacpp"
backend: str      # "flm:npu" | "llamacpp:vulkan"
ctx_size: int
embedding_model: str
verified: bool
```

It is scoped **per agent, one active per machine** ("a machine runs exactly one profile"). The whole design is: **promote that record from machine-scoped to task-scoped, and let a skill declare which one it needs.**

Nothing new is invented. Four things change:

| # | Change | Where |
|---|---|---|
| 1 | Skills declare a runtime requirement | `SKILL.md` frontmatter |
| 2 | A resolver maps requirement → `DeviceConfig`, with loud admission checks | new, small |
| 3 | The model broker gets N slots instead of 1 | `src/gaia/daemon/broker.py` |
| 4 | The daemon gains a task runner + async task contract | new |

### 1. Skill declares its runtime

Additive under the existing `metadata.gaia` block — same place as `tools_required` and `permissions`, so no new file format:

```yaml
metadata:
  gaia:
    tools_required: [read_file, summarize_document]
    runtime:
      requires:
        class: small-fast      # small-fast | general | reasoning | vision
        ctx: 8192
        tools: true            # needs native tool-calling
      prefer_device: npu       # hint, not a mandate
      pin: gemma4-it-e2b-FLM   # escape hatch: exact model, no substitution
```

**Declare a requirement; let the scheduler resolve a placement.** `requires` is portable — the same skill runs on a laptop NPU and a cluster dGPU. `pin` is the escape hatch for when a skill genuinely needs one checkpoint (a fine-tune, a specific quantization whose output differs) and must fail rather than substitute.

This is the one place the "never bind placement into a definition" rule bends, and only here. It holds for *agents* — binding a device to an agent identity means every new placement needs a new agent. But a **skill is the capability**, and the model requirement is a genuine property of the capability: summarization wants small-and-fast, image generation wants a GPU node, screenshot extraction wants a VLM. Binding at the skill is correct; binding at the agent is not.

### 2. Resolution, with loud admission

`requires` + detected hardware → a `DeviceConfig`. Because Lemonade's device targeting *is* the model registration (recipe = device), resolving to a `DeviceConfig` **is** choosing the backend. The existing record already encodes what Lemonade needs.

Admission runs at **submit**, not at run. Every one of these is a `CLAUDE.md`-style actionable error naming what failed, what to do, and where to look:

| Check | Why it must be a hard reject |
|---|---|
| Skill needs tools, resolved model has `tool_calling=False` | The FLM/NPU server **500-errors on an OpenAI `tools` payload** — verified on hardware (`lemonade_client.py:349-360`). A tool-heavy skill physically cannot bind to `gemma4-it-e2b-FLM`. |
| `requires.ctx` exceeds the device ceiling | FLM cannot load above `NPU_CTX_SIZE` (32768); handing it 65536 fails the load outright. |
| `pin`ned model not installed | Fresh-machine failure (#1655 class). Name the install command. |
| No `verified` config for detected hardware | Warn-and-degrade or reject — a product decision, but never silent. |

That first row is the sharpest constraint in the whole design and it is easy to miss: **the NPU model is not a general-purpose target.** It is right for classification, triage, summarization, and embedded-JSON work; it is wrong for anything tool-driven.

### 3. Multi-slot broker

Today `broker.py:128-152` arbitrates **one** slot, because Lemonade was treated as single-tenant. It isn't — `max_loaded_models` defaults to `1` but is config, and FLM-on-NPU can be resident and inferencing alongside llama.cpp-on-GPU (see the companion doc).

The broker becomes N slots keyed by `(device, model)`. Everything it already does carries over and gets *more* valuable: priority queueing (interactive > background), hot-model affinity (avoid needless evict+reload), TTL reclaim at 900s.

One addition it needs: **budget bytes, not slots.** On a shared-memory APU like Strix Halo the NPU and iGPU draw from one pool, and Lemonade does no VRAM-pressure detection on Windows/AMD. Counting slots will OOM the box. Admission has to know memory.

### 4. Task runner and the async contract

The blocker is small and absolute: **every tool call is synchronous, capped at 180s** (`DEFAULT_TOOL_TIMEOUT`, `agent.py:140`). Until a tool can return a handle, none of this runs.

```python
task = run_skill_task("summarize", inputs={...})   # returns immediately
task_status(task)    # queued | running | done | failed  (+ progress)
task_result(task)    # an artifact handle, not a payload
```

The runner, daemon-owned: resolve `DeviceConfig` → acquire the slot lease → construct a minimal agent with `model_id` from the resolved config, exactly one skill loaded, and only that skill's `tools_required` → run → write the result to the artifact store → release the lease.

The parent's transcript holds a task id and a status line. That is what lets a 32K window drive work of unbounded size.

## Why this is not sub-agent delegation

The distinction is load-bearing, and it is the reason this design works where delegation didn't.

| | Sub-agent delegation | Skill-bound task |
|---|---|---|
| Input | Free-text prompt the **parent model writes** | **Typed arguments** to an authored procedure |
| Procedure | Improvised by the child | Fixed in `SKILL.md`, human-authored and reviewed |
| Quality depends on | The parent's prompt-writing ability | The skill author |
| Failure mode | Vague prompt → wasted run, unrecoverable | Bad inputs → rejected at submit |

The fatal objection to delegation was that a 4B model must write a good cold-start prompt for a child that sees no conversation. **That objection is absent here** — nobody generates a prompt. The parent supplies arguments; the procedure is already written. This is why the same constraint that killed delegation leaves this design untouched.

## What already exists

More than half of it:

- **`DeviceConfig` + `DEFAULT_DEVICE_CONFIGS`** — the binding record, with per-device `embedding_model` already encoding the NPU co-residency lesson (#1744).
- **`ModelTier` / `build_model_tiers`** — precedent for "a capability declares a model preference list," which `requires.class` can reuse rather than reinvent.
- **The broker** — priority, hot-model affinity, TTL reclaim. Single-slot, but the arbitration is written.
- **Skills as the capability unit** with `tools_required` and `permissions` (#2995).
- **`is_tool_calling_model()`** (`lemonade_client.py:494`) — the admission check already has its predicate.
- **Sidecar supervision** — spawn, health-poll, tree-kill, ephemeral ports.

Missing: the async task contract, the multi-slot broker, the `runtime` frontmatter field, the resolver, and the artifact store.

## Build order

1. **Async task contract + task store.** The unblocking primitive; nothing works before it.
2. **`runtime` frontmatter + resolver + admission checks.** Pure logic, unit-testable with no hardware — and the admission rules are where the user-visible quality lives.
3. **Multi-slot, memory-budgeted broker.** Raise `max_loaded_models`; place by resolved config.
4. **Artifact store** (#1973, generalized) so results never enter the transcript.

Steps 1-2 are independently useful and testable on one device. Step 3 is where the parallelism arrives.

## Risks worth naming

- **Memory on unified-memory parts is unmeasured.** Two resident models contend for one pool and nothing detects the pressure. Bench it on the target box before step 3 ships.
- **`--parallel 1`.** Lemonade launches llama.cpp with one slot, so concurrent calls to the *same* model queue. Raising `-np N` without `-kvu` silently divides `ctx_size` — the #1030 failure mode. Different models on different devices are genuinely parallel; the same model is not.
- **Verified-matrix growth.** `DeviceConfig.verified` is per (agent, device) today. Skill-bound execution makes it per (skill, device), and `CLAUDE.md` requires an eval for LLM-affecting surfaces. Decide what "verified" means for a skill-runtime pair before shipping a matrix nobody can run.
- **Fresh-machine failure.** A skill pinning a model `gaia init` never pulled fails only on a cold box — exactly #1655. Skill install must reconcile against installed models, and the admission check must run before the task is accepted.

## Open questions

- **Does `requires.class` resolve against a static table or detected hardware?** A static table is predictable; hardware detection is portable. `DEFAULT_DEVICE_CONFIGS` suggests static, with detection filtering it.
- **Can a task chain?** A skill that submits another skill's task is a job DAG, not delegation — but it needs cycle detection and a depth cap before it's safe.
- **Where do remote/cluster targets appear?** Cleanest is a `DeviceConfig` whose device is a cluster queue, so the resolver is unchanged and only the runner differs.
- **Does a task inherit the caller's permissions?** The skill declares `permissions`; the caller has grants. Deny-only inheritance is the safe default — a task can never exceed the grants of whoever submitted it.
