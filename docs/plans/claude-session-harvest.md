# Harvesting Claude Code sessions into GAIA evals and skills

**Status:** Proposed
**Date:** 2026-08-19
**Target agent:** `ChatAgent` (`hub/agents/chat/python/gaia_agent_chat/`) — the flagship
**Milestone:** Agent Factory M2/M3 (see [`agent-factory.md`](agent-factory.md))

---

## Why this matters

Claude Code session transcripts accumulate in `~/.claude/projects/`. They
record, turn by turn, how a frontier model actually does work on this codebase. Today
they are exhaust. This plan turns them into a **coding eval dataset** that says whether
the local agent harness works at all, and a set of **skills** that transfer procedures a
big model discovered onto a 4B local model that would never derive them alone.

The pieces this needs mostly exist. ChatAgent already logs every tool call to
`tool_history` in `~/.gaia/memory.db`; `skill_synthesis.py` already reads exactly that
table and emits `SKILL.md`; `gaia eval agent --agent-type chat` already drives ChatAgent
through the UI server. The work is an **ingest adapter plus a verifier**, not a new
pipeline.

Only code ships to the repo. The corpus and everything derived from it stays in
`~/.gaia/cache/factory/`.

---

## What was verified on HEAD

### The target

`ChatAgent` is the flagship. The `GaiaAgent` rename (#696) has not landed. The recent
ProfileSpec refactor (#2323/#2362) split it into profiles — `chat`, `doc`, `file`,
`data`, `web`, `full` — with `full` the default, declaring 54 tools.

| | Value | Source |
|---|---|---|
| Model | `Gemma-4-E4B-it-GGUF` | `DEFAULT_MODEL_NAME` |
| Context | 32768 | `agent.py` (`min_context_size`) |
| Max steps | 50 (`GAIA_AGENT_MAX_STEPS` overrides) | `DEFAULT_MAX_STEPS` |
| Memory | `~/.gaia/memory.db`, `tool_history` auto-logged per call | `memory_store.py` |
| Eval | `gaia eval agent --agent-type chat` via UI server :4200 | `eval/runner.py` |
| Skills | machinery inherited, **never called** | no `skills:` block in `gaia-agent.yaml` |
| Code index | **absent** (CodeAgent has it, ChatAgent does not) | `agent.py` mixin list |

### Tool mapping — good on edits, weak on search

| Claude Code | ChatAgent | Fidelity |
|---|---|---|
| `Read` | `read_file(file_path)` | 1:1 |
| `Write` | `write_file(file_path, content, create_dirs, project_dir)` | 1:1 |
| `Edit` | `edit_file(file_path, old_content, new_content, project_dir)` | 1:1 semantics |
| `Bash` | `run_shell_command(command, timeout, cwd)` | 1:1 |
| `Grep` | `search_file` / `search_directory` / `search_file_content` | **~70%** — substring not regex, split across three tools, no `.gitignore` |
| `Glob` | `find_files` (fnmatch) / `browse_directory` | **~70%** — no glob syntax |
| `Task` (subagents) | — | none |
| `WebSearch` / `WebFetch` | `open_url`, `fetch_webpage` (no search) | partial |

Edit/read/write/shell replay cleanly. **Search does not**, and search is where Claude Code
sessions are densest. Expect that to be the dominant translation loss — and possibly the
single most valuable finding of Phase 0, since "our search tools are the bottleneck" is
actionable in a way "the model is small" is not.

### The synthesis pipeline is already built and already fed

`skill_synthesis.py` implements DETECT → CLUSTER → DISTILL → RECONCILE and emits
`SKILL.md` via `DistilledProcedure.to_skill_md()`. DETECT is `extract_sequences()`, a thin
adapter over `MemoryStore.iter_sessions()`, which returns:

```
{session_id, goal, tools, tool_sequence, success_count, attempt_count, started_at, last_at}
```

A Claude Code transcript maps onto that directly — `sessionId`, first user message,
ordered `tool_use` blocks, success from whether the paired `tool_result` carried
`is_error`. Everything downstream (clustering at `SIMILARITY_TAU=0.82`, distillation at
`DISTILL_MAX_TOKENS=1024`, reconciliation into the `procedures` table) runs unchanged.

Better: `ProceduralMemoryMixin._recalled_skill_tools()` is already wired into ChatAgent's
dynamic tool loader, so a recalled procedure already influences runtime tool availability.
Synthesis exists but is **manual/off** — this plan gives it something to chew on.

---

## Five problems that will sink this if ignored

### 1. Oracle contamination — the one that matters

The Agent Factory spec is explicit that the eval gate rests on "test cases curated by a
human who did not write the spec, stored where the optimization loop cannot read them,"
because "an LLM that writes the agent *and* its test data *and* grades the result
certifies nothing."

Mining skills and eval scenarios from the same sessions violates that directly.
Distil a procedure from session X, test on a scenario mined from session X, and the score
measures memorization.

**Mitigation, applied before anything is generated:** partition on a deterministic hash of
the session id — `sha256(session_id)[:8] % 100 < 30` to the oracle, the rest to the
distill pool. The harvester writes two directories; the distiller is given no path to the
oracle. Cheap, auditable, and it must land in Phase 1 rather than be retrofitted.

Even then, an auto-mined oracle is a **weaker** gate than M2's human-curated one. Label it
a regression detector, not a release gate.

### 2. Skills-without-a-gate is already a decided question

#2848 shipped the email agent with **Agent Skills off until an eval gate covered them**.
That is direct precedent: this project does not ship skills to users without eval
evidence. It makes the eval half of this plan a **prerequisite** for the skills half, not
a parallel track. Phases are ordered accordingly.

### 3. ChatAgent does not load skills today

The base `Agent` has `load_skill()`, `load_declared_skills()`, `load_skill_set()`, and
ChatAgent inherits all of it — but never calls any of it, and its `gaia-agent.yaml` has no
`skills:` block. Phase 5 is therefore a **code change to the flagship**, not a matter of
dropping files in a directory. Budget it as such.

When those skills are authored, two gates apply. `metadata.gaia.permissions` treats
`filesystem:*` and `shell:*` as unbridged — `refuse_unbridged_permissions()` rejects them
outright. Generated coding skills must declare `tools_required` (ungated) with an **empty
`permissions` list**. And every generated skill must clear `gaia skill audit`, where an
unparseable file forces `REVIEW` at any tier — a reason to keep generated skills
prose-only, with no `tools.py`.

### 4. Seeded memory is memory of things the agent never did

Writing harvested traces into `tool_history` makes `~/.gaia/memory.db` assert that
ChatAgent performed those tool calls. It did not — Claude did, with different tools. Left
unmarked, `recall` and `search_past_conversations` will confidently report fabricated
first-person history to the user.

Seeded rows need a provenance marker (a reserved `session_id` prefix at minimum, a
`source` column ideally) that recall paths filter on, so imported procedure is usable for
synthesis without being claimable as experience. Note this also touches user state
outside the cache, so `gaia factory` needs an unseed path.

### 5. The capability gap is real, and it is steps and search, not raw context

A typical session opens with 60K+ tokens of cache-creation and runs past 100 tool calls.
ChatAgent allows 50 steps at 32768 context. The decision was to include everything and
accept a low first baseline — right for finding the wall, provided the scorecard separates
**out of steps**, **context overflow**, **unmappable tool**, and **genuinely wrong**. Only
the last is a model-quality signal; the rest are engineering.

Expect a first baseline in the 10–25% range. That is the honest starting point.

---

## Where the code lives

Everything under the factory namespace, per the factory spec's `gaia factory <recipe>`
orchestrator (listed there as "to build").

```
src/gaia/factory/
├── cli.py                    # `gaia factory {harvest,distill,pack,run,seed,report}`
├── harvest/
│   ├── reader.py             # Claude Code JSONL → normalized Trace
│   ├── schema.py             # Trace / Step / Outcome dataclasses
│   ├── scrub.py              # secrets, absolute paths, internal identifiers
│   ├── translate.py          # Claude tools → ChatAgent's 54; flags unmappable
│   └── partition.py          # the hash split; oracle never leaves this module
├── distill/
│   └── adapter.py            # Trace → iter_sessions() shape → skill_synthesis
├── pack/
│   ├── scenario.py           # Trace → eval scenario YAML
│   └── fixture.py            # git fixture repo per scenario
└── seed/
    └── memory.py             # provenance-marked tool_history rows + unseed
```

**Cache layout — never checked in:**

```
~/.gaia/cache/factory/
├── traces/   oracle/   pool/   skills/   fixtures/   runs/
```

Wire `factory` into `gaia cache status` / `gaia cache clear` so it is discoverable and
disposable.

### The Claude Code skill that drives it

`.claude/skills/harvesting-claude-sessions/SKILL.md` — teaches Claude Code to operate the
above: subcommand order, how to read the Phase 0 histogram and decide what is in envelope,
how to review a generated skill before promotion, and the hard rule that the oracle is
never read during distillation. A *driver* skill: parsing logic lives in tested Python;
the skill carries judgment. GAIA already discovers `.claude/skills` read-only via the
`claude-import` root, and this one is authored for Claude Code — it never has to run on
Gemma.

---

## Phases

**Phase 0 — Cold read, no shipped code.** How much of the corpus is even in envelope? Per
session: tool-call count, largest single tool result, and use of unmappable tools
(`Task`, `WebSearch`) or regex-`Grep` that `search_file` cannot express. Histogram it.
Go/no-go: fewer than ~80 sessions under 50 tool calls with a clean tool mapping means the
unit must be *sub-spans* of sessions, not whole sessions — which changes the harvester's
design, so this is worth doing first.

**Phase 1 — `gaia factory harvest`.** Deterministic, no LLM. JSONL → scrubbed normalized
traces, partitioned into `oracle/` and `pool/`. The scrubber is the risk: absolute paths,
`gitBranch`, AMD-internal identifiers, anything token-shaped. Test with a fixture
transcript containing planted secrets and assert none survive.

**Phase 2 — `gaia factory pack`, and the eval runs first.** From `oracle/` only. Each
scenario needs a git fixture repo pinned at the session's starting commit, the user's
objective as the prompt, and a **deterministic verifier** — tests pass, file contains
string, diff applies. Not an LLM judge.

The existing `eval/scenarios/` tree does not fit: it is doc-QA shaped with personas and
corpora, and the judge scores seven conversational dimensions. The right base is
`src/gaia/eval/behavior_harness.py`, whose `true_success` / `false_success` /
`honest_failure` verdicts answer exactly "did the agent change the file, or only claim
it did." Extend the scenario schema with `setup.workspace` and `verify.*` rather than
bending `index_documents`.

Run via the existing `--agent-type chat` path against the UI server on :4200. One eval at
a time — the serial-eval rule in CLAUDE.md applies. Save a baseline.

**Phase 3 — `gaia factory distill`.** Adapter emits the `iter_sessions()` shape from
`pool/`; existing `cluster_by_goal` → `distill_cluster` → `to_skill_md` runs unchanged.
Post-process to force empty `permissions`, populate `tools_required` from the translated
toolbelt, set `security_tier: experimental`. Gate every output through
`gaia skill audit`. Follow `migrate.py`'s field-mapping and lossless-preservation pattern
rather than duplicating it.

**Phase 4 — The A/B that answers the actual question.** Same oracle, two runs: skills off,
skills on. The delta is the number — does transferring frontier-model procedure onto a 4B
model measurably improve it? Report per difficulty bucket so a gain on simple edits is not
hidden by a wall on hard tasks. This is also the eval gate #2848 demands before skills
ship to users.

**Phase 5 — Day-1 seeding.** Two halves, both gated on Phase 4 showing a positive delta:

- *Skills:* wire `skills:` / `skill_sets:` into ChatAgent's `gaia-agent.yaml` and call
  `load_declared_skills()` at startup. Promote only positive-delta skills.
- *Memory:* `gaia factory seed` writes provenance-marked `tool_history` rows so
  `synthesize_procedures()` has history to work on and `_recalled_skill_tools()` surfaces
  the results. Ship `unseed` alongside it.

---

## Risks

| Risk | Handling |
|---|---|
| Scrubber misses a secret | Corpus never leaves `~/.gaia`; planted-secret fixture test; scrub before partition |
| Oracle leaks into distillation | Hash partition in Phase 1; distiller has no oracle path; auditable after the fact |
| Auto-mined oracle is a weak gate | Labelled a regression detector; M2's human oracle still owed |
| Seeded memory read as first-person experience | Provenance marker filtered by recall paths; `unseed` shipped with `seed` |
| Search-tool divergence dominates the loss | Measured in Phase 0 before building; may reframe the outcome as a tools finding |
| Generated skills are noise | `gaia skill audit` + human review before promotion; only positive-delta skills ship |
| Baseline too low to read | Failure modes bucketed so harness limits are separable from quality |
