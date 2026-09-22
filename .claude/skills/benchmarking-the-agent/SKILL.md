---
name: benchmarking-the-agent
description: Measure the flagship GAIA agent against Claude Code and across models — quality, truthfulness, steps, tokens, time and real cost — using the task battery, the adversarial suite and the LLM judge. The recipes need the local `~/gaia-sweep/bench` harness, which is not in this repository; without it only the traps and how to read a result apply. Use when asked to benchmark the agent, compare models, check whether a change cost quality, work out what a run costs, or reproduce the model comparison table.
---

# Benchmarking the flagship agent

The harness runs the same tasks through GAIA and through Claude Code, scores them
mechanically, then has an LLM judge grade the transcripts. It answers three questions:
**does the work get done**, **is the answer honest about it**, and **what did it cost**.

Everything here was learned by running it. The traps section is the part that saves a day.

## Where things live

| What | Where |
|---|---|
| Harness | `~/gaia-sweep/bench` (not in the repo — it is local tooling) |
| Integration checkout under test | `~/gaia-sweep/bench-fixes` (a worktree of amd/gaia) |
| Task workdirs while running | `/Users/Shared/gaia-bench-work/runs*` — outside the bench tree on purpose |
| Per-run copies kept for judging | `~/gaia-sweep/bench/runs.<tag>/` |
| Mechanical results | `results.<batch>.<tag>.jsonl` |
| Judge output | `judge2.<tag>.jsonl` |

`env.sh` sets the live Lemonade port and key, `GAIA_HOME`, the memory DB and
`GAIA_AGENT_MAX_STEPS`. **The batch runners source it themselves; anything you run
standalone does not** — so `. ./env.sh` first before `judge2.py`, `cc_trace.py`, or
anything touching `$LEMONADE_BASE_URL`. It also puts the project toolchain on the agent's PATH —
without it `pytest` is missing, every verification is refused for the wrong reason, and
the benchmark scores a harness failure as the agent's. `BENCH_ENV=broken` withholds it
deliberately, as the adaptability condition.

## The one rule: serial

Every runner wipes and reuses a shared `BENCH_WORK_ROOT`. **Two batteries at once
destroy each other's workdirs.** Before starting anything:

```bash
# Bracketed so the pattern does not match the shell running the check itself.
pgrep -fl "[r]un_task.py|[r]un_full_branch.sh|[c]c_trace.py|[j]udge2.py" | wc -l   # must be 0
```

If something is in flight, **queue rather than wait at the keyboard**: copy the wait loop
at the top of `run_slate.sh` (`while pgrep -f …; do sleep 60; done`) into your own script
and launch it with `nohup`. Budget 4–9 minutes per model for the 14-task battery
depending on the model's speed, plus about 2 minutes to judge it.

The same applies to `gaia eval agent`, for a different reason (CLAUDE.md: concurrent
evals race-evict each other's models on the local Lemonade slot). Never `pkill` a run
mid-task, and never stop Lemonade or the daemon while one is in flight.

## Recipes

**A branch against Claude Code, 14 tasks, judged.** The main loop of any harness change:

```bash
cd ~/gaia-sweep/bench && GAIA_SRC=~/gaia-sweep/bench-fixes TAG=mytag ./run_full_branch.sh
```

Runs `batch1_basics`, `batch3_github`, `batch6_usecases` against the checkout in
`GAIA_SRC`, copies the workdirs to `runs.mytag/`, and judges. `MODEL=` picks the model
(default Kimi K2.7 Code).

**Several models, one at a time.** `run_slate.sh` waits for anything in flight, then runs
each `model:tag` pair through the battery and judges each:

```bash
GAIA_SRC=~/gaia-sweep/bench-fixes ./run_slate.sh \
  fireworks.glm-5p3-flash:glm53flash fireworks.deepseek-v4p1-flash:v4p1flash
```

It pins `RUN_TIMEOUT=900`. Do not lower that — see the traps.

**The adversarial suite** (12 tasks: rate limits, a missing `gh`, prompt injection in an
issue, a lying README, a hanging test, an issue that does not exist):

```bash
TAG=adv7 LEG=gaia MODEL=fireworks.glm-5p3-flash ./run_adversarial.sh   # or LEG=cc
python3.12 judge2.py /Users/Shared/gaia-bench-work/runs-adv adv7 \
  --agent-dir fireworks_glm-5p3-flash --baseline /Users/Shared/gaia-bench-work/baseline --batch 6
GAIA_DIR=fireworks_glm-5p3-flash python3.12 adversarial_report.py adv7 <cc-tag>
```

These must be serial even among themselves: the `gh` stand-in keeps one state file.

**Claude Code at any model**, as a reference leg:

```bash
export BENCH_WORK_ROOT=/Users/Shared/gaia-bench-work/runs-ccopus
RESULTS_TAG=cc-opus python3.12 cc_trace.py claude-opus-5     # or claude-sonnet-5
cp -R "$BENCH_WORK_ROOT" runs.cc-opus
python3.12 judge2.py runs.cc-opus cc-opus --agent-dir cc-opus --baseline /Users/Shared/gaia-bench-work/baseline
```

Claude Code reports its own spend, so its cost column is real, not rate-carded.

**Report.** `slate_report.py` ranks any set of tags by judged quality, with cost as a
percentage of the Claude Code reference:

```bash
python3.12 slate_report.py "glm53flash-<rev>=GLM-5.3 Flash" "kimi7-<rev>=Kimi K2.7"
```

## Reading a result honestly

- **One run is noise.** A single model's quality spans about 0.3 across repeat runs
  (Kimi measured 4.50–4.83 on an unchanged branch). Never report a delta below that from
  n=1. `run_repeats.sh` queues repeat runs behind whatever is in flight.
- **Per-task deltas locate a regression**; the aggregate only tells you one exists. Diff
  the judge's per-task scores between two tags and read the `one_line` for the worst.
- **Then check the transcript, not your theory.** `judge2._gaia_calls(transcript)` and
  `_cc_calls` yield `(name, args, result)` per call. Count what the agent actually did
  before blaming a change: twice I had a confident hypothesis (trimmed tool descriptions;
  tool ordering) and both died on inspection — the descriptions kept their key
  instructions, and the tool order was byte-identical.
- **Watch for confounds.** After compound shell commands landed, `gh` invocations halved
  while the same work got done in chained calls. A raw call-count drop is not a
  behaviour regression.

## Traps that cost real time

**`RUN_TIMEOUT` is a wall-clock cap, so it silently penalises slow models.** At the old
420 s default, GLM-5.3 full averaged 182 s per task and got cut off three times — it
scored 40% and looked incapable. Use 900 s for every model in a comparison, high enough
that none of them binds. (GLM-5.3 full still took 864 s at the higher cap; it really is
slow. The point is you cannot tell which until the cap stops binding.)

**Rate cards drift, and a wrong one silently rewrites every cost claim.** My table had
DeepSeek V4.1 Flash at `$0.22/$0.007/$0.66` when the published card was
`$0.30/$0.006/$1.20` — every Flash cost I had reported was ~30% low. Verify against
`docs.fireworks.ai/serverless/pricing` before quoting cost, and price a dated snapshot
that has fallen off the list (`deepseek-v4-flash-0731`) at its successor's card so the
comparison never flatters it. Cards live in `ab_report.py::RATES`; a model with no
published card must print `n/a`, never a guess.

**Graders must not write into the agent's workdir.** A probe that writes `data.txt` makes
the judge blame the agent for a stray file. `run_task.graded_copy()` runs tests and
probes in a throwaway copy — keep new probes inside it.

**Keyword rubrics are both lenient and brittle.** They pass an answer containing the
right word without it establishing the point, and fail a correct answer that phrases it
differently. Judged criteria (`must_establish`, graded via `judge2.py`'s
`answers_correctly`) replaced them; switching moved the adversarial score by several
points in both directions. Do not add new keyword checks.

**The judge often costs more than the run it grades.** A 14-task judge pass is
$0.4–0.9; GLM-5.3 Flash's entire run is $0.083. Batch it (`--batch 6`) and do not re-judge
a tag you have already judged.

**Fresh input dominates GAIA's cost**, and the first call of every task is uncached
because each task is a new process. Within a process caching reaches ~95%; across
processes Fireworks gives a new one nothing, while Claude Code's account-wide cache hits
91% across its runs. That asymmetry flatters Claude Code's token efficiency and is worth
stating when reporting.

**Diff the actual requests when caching looks wrong.** `GAIA_BENCH_DUMP=<file>` records
every chat request and response; compare consecutive system prompts and tool arrays
byte-for-byte. That is how the random per-process scratch-directory name — one line in an
otherwise identical system prompt — turned out to be defeating the prefix cache.
`check_wire.py <dump>` reads the same log for a different question: whether the model was
shown its own tool calls and got each result back correctly paired, exiting non-zero on a
request an OpenAI-style server would reject.

## Adding a model

1. Confirm it is live: `curl -s "$LEMONADE_BASE_URL/models" -H "Authorization: Bearer $LEMONADE_API_KEY"`.
2. Add its published card to `ab_report.py::RATES`, keyed by the bare model name.
3. Run it through `run_slate.sh` alongside at least one model you already have numbers
   for, so a harness change cannot be mistaken for a model difference.

## What good looks like

Numbers below are a snapshot from September 2026 — treat them as the shape to expect, and
re-measure rather than quoting them.

**Run Claude Code at Opus 5 as the reference, not Sonnet 5.** Opus is the strongest leg
available, so a cost share reads as a fraction of the best agent rather than of a
mid-tier one, and it sets an honest quality ceiling. On the 14-task battery Opus scored
14/14 at quality 4.97, truthfulness 5.00, 83 steps, 456 s, $3.89; Sonnet 5 scored 4.91 for
$1.26, which is 32% of Opus.

Against that ceiling, GLM-5.3 Flash reached 14/14 at quality 4.96 for $0.083 — within
0.01 of Opus at 2.1% of its cost — and DeepSeek V4.1 Flash 4.88 for $0.105. A model that
costs more than the Sonnet reference and scores below it (Qwen3.8 Max, 4.57, $1.325) is
not a candidate, however good its public benchmarks look.

Truthfulness is where the daylight is: Opus and DeepSeek V4 Pro both score a clean 5.00,
most others sit at 4.86. That axis — never claiming what the tool record does not
support — is the one worth optimising next.
