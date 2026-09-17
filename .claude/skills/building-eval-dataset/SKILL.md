---
name: building-eval-dataset
description: Build or regenerate the step-level agentic eval dataset from Claude Code session transcripts, and run a candidate agent harness against it. Use when asked to generate the eval dataset, label sessions by use-case, evaluate a harness or the GAIA agent step by step, or reproduce the dataset on another machine.
---

# Building and running the step-level eval dataset

Turns a local Claude Code transcript corpus into **decision-point records** — one per
moment the agent had to choose its next action — and grades a candidate harness against
them.

The parsing is deterministic Python (`gaia.factory.dataset`). **An LLM is used only where
judgement is genuinely required, and it runs as a Claude Code subagent — never as a direct
API call.** There is no `ANTHROPIC_API_KEY` path and none is needed.

## Privacy — before anything else

Transcripts contain absolute paths, usernames, branch names, repository content, and
whatever was pasted into a prompt. `amd/gaia` is public.

- Everything derived goes to `~/.gaia/cache/factory/`. **Never** into a repo.
- Never paste a record, excerpt, or sample into an issue, PR, commit, or doc.
- Report aggregates only.
- The dataset is **private-tier even after scrubbing** — a regex cannot remove arbitrary
  proper nouns.

Three defences exist and all three are automatic: the output path is outside every repo,
`build.py` refuses to write into a git working tree, and `.gitignore` plus
`tests/unit/factory/test_dataset_gitignore.py` cover the rest.

## Never run the agent under test from this repo

This skill's own description tells a reader it is being evaluated, and subagents see skill
descriptions. Start the agent under test from a neutral directory with no `.claude/` above
it — not this repo, not the experiments directory. A score from a run that could see this
file is directional at best.

---

## Run it

```bash
cd <gaia-worktree>
export PYTHONPATH=$(pwd)/src      # the editable install may resolve to another worktree
```

### 1. Extract — deterministic, no LLM

```bash
python -m gaia.factory.harvest.scan
```

Writes `traces.jsonl`, `intents.jsonl`, `stats.json` to `~/.gaia/cache/factory/`.

**Optional but recommended:** freeze a copy (`cp -r ~/.gaia/cache/factory/*.jsonl
~/.gaia/cache/factory/snapshot-YYYY-MM-DD/`). The corpus grows while you work on it, and
Claude Code prunes old transcripts — 31 of one frozen 300-session list had already
vanished. Building from a frozen copy is the only way to reproduce a number later.

### 2. Label — this is your job, not a script's

`build.py` requires `labels.txt` and **will not run without it**. There is no labelling
script on purpose: assigning a use-case is a judgement call.

1. Read `intents.jsonl`. Split into batches of ~70 sessions.
2. For each batch, dispatch a subagent with **the same taxonomy every time** (the 19
   use-cases in `§2` of the reference analysis: `pr_lifecycle`, `code_review`,
   `doc_audit`, `doc_authoring`, `feature_impl`, `bug_fix`, `refactor`, `ci_debug`,
   `security_fix`, `test_coverage`, `eval_quality`, `release_packaging`, `repo_ops`,
   `research`, `live_validation`, `meta_agent_config`, `data_transform`,
   `qa_conversational`, `other`). Ask for strict JSON: one primary use-case plus up to two
   secondary tags per session.
3. Write `~/.gaia/cache/factory/labels.txt` as
   `<8-char-session-prefix> <primary> <secondary,secondary>`.

**Classify from the first user message, never the auto-generated title.** The title is a
summary of what happened, so using it leaks the outcome into the label.

Batches can run in parallel — give every one identical instructions or the taxonomy
drifts between batches and the use-case counts become meaningless.

### 3. Build

```bash
python -m gaia.factory.dataset.build --username <account-name>
# --snapshot <dir>   build from a frozen copy instead of the live scan
# --extra-name "…"   repeatable; redact a colleague's name (a regex cannot infer these)
```

Writes `oracle/`, `pool/`, `blobs/`, and the manifests. The build **ends by running the
integrity audit and the leak sweep and aborts if either fails** — there is no override.

### 4. Verify anytime

```bash
python -m gaia.factory.dataset.audit                        # 16 integrity invariants
python -m gaia.factory.dataset.verify --username <account>  # privacy leak sweep
python -m gaia.factory.dataset.verify --spot-check 5        # records to hand-check
python -m pytest tests/unit/factory/ -q
```

---

## Running a harness against it

Full contract, field reference and worked examples:
`~/.gaia/cache/factory/dataset/STATUS_AND_USAGE.md`. Read it before writing any grading
code. Four things matter most.

**Use the shipped graders.** `gaia.factory.dataset.verifiers.grade`. Do not write your
own — the shipped ones are calibrated against the reference and the record's
`reference_checks` only make sense with them.

**Smoke-test first.** Replay `record["action"]["calls"]` as if it were the candidate's
proposal. You must get **100% credited on `match_reference` and 100% on all four checks
over informative records**. Anything less means the integration is wrong, not the harness.
Do this before evaluating anything real.

**`pool/` for development, `oracle/` only for a final number.** The split exists so a
harness tuned on one is measured on the other. Reading `oracle/` while iterating destroys
the only contamination control the dataset has.

**Three rules that decide whether the numbers mean anything:**

- **Two polarities, never merged.** 147 records are `avoid_reference`: the reference
  action *failed*, so reproducing it scores zero and the harness wins by doing something
  else that passes the checks. Timeouts are 24.8% of corpus failures — crediting a
  reproduction would credit the worst habit in the data.
- **Only score a check where the reference passed it** (`checks_informative`). Ignoring
  this costs a harness ~30% on `old_string_present` for the dataset's own staleness.
- **Never emit one aggregate score.** Report per axis and per use-case, with check
  coverage beside every rate.

### Evaluating GAIA specifically

GAIA's tool names differ from Claude Code's — map them or `tool_selection` scores zero on
everything. `Read`/`Write`/`Edit` → `file_io`; `Grep`/`Glob` → `file_search` or
`code_index`; `Bash` → `shell`; `WebSearch`/`WebFetch` → `browser`. `Agent`/`Task` has no
GAIA equivalent, so the `delegation` axis (137 records) is unscoreable — report that as a
capability gap rather than a zero.

---

## Honesty requirements

Not optional; the evaluation is worthless without them.

- **There is no ground truth for task success.** Nothing in a transcript says whether the
  human's goal was met. `episode_outcome` is inference with stated evidence and
  confidence — 1,688 of 3,296 records are `unknown` because 67% of sessions have exactly
  one human turn and there is no reaction to read. Use it to rank; never quote it as fact.
- **The reference is what one strong harness did, not what was optimal.** Report
  *agreement*, never *accuracy*.
- **Reasoning quality cannot be graded.** Extended thinking is encrypted corpus-wide —
  zero of 31,935 decision points have recoverable thinking text, though 55% reasoned
  invisibly. `reasoning.inferred` reconstructs the decision *context*, not the model's
  thoughts. Do not build a reasoning judge on it.
- **This is a regression detector, not a release gate.** An auto-mined oracle is weaker
  than a human-curated held-out set. Exact action overlap across the split is 0.87%, but
  65.9% of sessions share a repository branch across it.
- **One user, ~6 repositories, 37 days.** A portrait of one developer, not of developers.
- **Four use-case/partition groups sit below the sampling floor.** Do not report
  per-use-case scores for them; `DATASHEET.md` names them.

---

## The traps that will silently corrupt a rebuild

Each cost real debugging time. `.claude/skills/analyzing-claude-sessions/SKILL.md` covers
five more for the extraction layer; these are specific to this dataset.

1. **Subagents are not sessions.** A delegated run lives in
   `<session-uuid>/subagents/*.jsonl` and a `*/*.jsonl` glob misses it — they are a third
   of all records here.
2. **One API response is several JSONL records**, one per content block. A message's text
   and its `tool_use` land in *different* records, so a per-record scan finds zero
   reasoning and concludes there is none. Group by `message.id`.
3. **Assign `step_index` when a message first dispatches a tool**, not when the message is
   created — otherwise two decision points share an index and a record's own action shows
   up inside its own `recent_steps`.
4. **`tools_available` must not be the transcript's tool union.** That is a union over the
   whole run including the answer; it once made 4.6% of records advertise exactly the
   tools used. Build it from environment facts instead.
5. **Hash `arg_hash` over the *scrubbed* arguments**, or the shipped record carries a hash
   that does not describe its own contents.
6. **`Glob` takes globs, `Grep` takes regexes.** Compiling `**/*.go` with `re` rejects
   every ordinary glob and makes the checker look 84% wrong when it is 100% right.
7. **Inline script bodies are not shell.** Splitting a `python -c "…"` or heredoc on
   `;`/newline yields "binaries" like `open(p`.
8. **Keep held file content fresh.** After the agent edits a file, the last-read copy is
   stale — 94% of `old_string` check failures were that, not bad arguments.

## Verifying a rebuild

Run the reference through its own graders. If it does not score 100% on informative
records, a *verifier* is wrong, not the data. That check found four separate bugs that
unit tests and the leak sweep both missed, because nothing failed — the graders simply
disagreed with reality.
