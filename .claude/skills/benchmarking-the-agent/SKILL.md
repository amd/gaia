---
name: benchmarking-the-agent
description: Measure the flagship GAIA agent against Claude Code and across models — quality, truthfulness, steps, tokens, time and real cost — with `gaia eval tasks`. Use when asked to benchmark the agent, compare models or harnesses, check whether a change cost quality, work out what a run costs, or reproduce the harness × model table.
---

# Benchmarking the flagship agent

`gaia eval tasks` runs the same tasks through GAIA and through Claude Code,
scores them mechanically, then has an LLM judge grade the transcripts. It
answers three questions: **does the work get done**, **is the answer honest
about it**, and **what did it cost**.

Everything here was learned by running it. The traps section is the part that
saves a day. The full reference is
[`docs/reference/eval.mdx`](../../../docs/reference/eval.mdx).

## The suites

| Suite | What it is |
|---|---|
| `everyday` | The 14 tasks a developer actually asks for. The comparison table's suite. |
| `therock` | Four real merged fixes in TheRock, from the commit before the fix. Two forbid the internet in the prompt. |
| `core` / `full` / `adversarial` | What CI gates on: the traps and the committed expectations. |

## The one rule: serial

Every run wants the local Lemonade model slot, and two at once race-evict each
other's models — the same reason CLAUDE.md gives for `gaia eval agent`. Before
starting anything:

```bash
pgrep -fl "[g]aia eval tasks" | wc -l   # must be 0
```

Budget 4–9 minutes per model for the 14-task suite depending on the model's
speed, plus about 2 minutes to judge it. Never `pkill` a run mid-task, and
never stop Lemonade or the daemon while one is in flight.

## Recipes

**One model, three runs, real cost.** The main loop of any harness change:

```bash
gaia eval tasks run --suite everyday --model fireworks.glm-5p3-flash \
  --repeats 3 --meter fireworks --out runs/gaia-glm
```

**The same model through Claude Code**, so the difference is the harness:

```bash
gaia eval tasks run --suite everyday --harness claude-code \
  --model fireworks.glm-5p3-flash --repeats 3 --out runs/cc-glm
```

**Claude Code on Anthropic's own model**, as the reference row. Run Opus 5, not
Sonnet: it is the strongest leg available, so a cost share reads as a fraction
of the best agent and it sets an honest quality ceiling.

```bash
gaia eval tasks run --suite everyday --harness claude-code --model claude-opus-5 \
  --out runs/cc-opus
```

**The TheRock tier**, a real 144K-line codebase:

```bash
gaia eval tasks run --suite therock --model fireworks.glm-5p3-flash --out runs/gaia-therock
```

**The table.** The first directory is the 100% row:

```bash
gaia eval tasks report runs/cc-opus runs/gaia-glm runs/cc-glm --out runs/report
```

**The judge's own controls**, before you trust any quality number:

```bash
gaia eval tasks controls
```

## Reading a result honestly

- **One run is noise.** A single model's quality spans about 0.3 across repeat
  runs. Never report a delta below that from n=1 — use `--repeats 3`, and read
  the min–max the table prints under each mean. A gap smaller than the range it
  sits in is noise.
- **Cost is three different kinds of dollar.** `metered` is real money;
  `api_equivalent` is Claude Code's list-price figure on a subscription, which
  is a price of compute and not money spent; `harness_counts` is a rate-carded
  estimate. The reports label them. Never sum or rank them together without
  saying which is which.
- **Per-task deltas locate a regression**; the aggregate only tells you one
  exists. Diff the per-task rows between two runs and read the judge's
  `one_line` for the worst.
- **Then check the transcript, not your theory.**
  `gaia.eval.bench.transcripts.tool_calls(transcript)` yields `(name, args,
  result)` for either harness. Count what the agent actually did before blaming
  a change: twice a confident hypothesis (trimmed tool descriptions; tool
  ordering) died on inspection.
- **Watch for confounds.** After compound shell commands landed, `gh`
  invocations halved while the same work got done in chained calls. A raw
  call-count drop is not a behaviour regression.

## Traps that cost real time

**A wall-clock cap silently penalises slow models.** At a 420 s cap, GLM-5.3
full averaged 182 s per task, got cut off three times, scored 40% and looked
incapable. The default is 1800 s; keep `--run-timeout` the same for every model
in a comparison and high enough that none of them binds.

**Rate cards drift, and a wrong one rewrites every cost claim.** A table once
had DeepSeek V4.1 Flash at `$0.22/$0.007/$0.66` when the published card was
`$0.30/$0.006/$1.20` — every Flash cost was ~30% low. Verify against
docs.fireworks.ai/serverless/pricing before quoting cost. Cards live in
`src/gaia/eval/bench/metering.py::RATES`; a model with no published card prints
`n/a`, never a guess. `--meter fireworks` bypasses the question entirely.

**A metered run is account-wide.** Two metered runs on one account at the same
time pollute each other's deltas, and the meter trails by ~90 s.

**Keyword rubrics are both lenient and brittle.** They pass an answer
containing the right word without it establishing the point, and fail a correct
answer phrased differently. `must_establish` judged by the LLM replaced them.
Do not add new keyword checks.

**The judge often costs more than the run it grades.** A 14-task judge pass is
$0.4–0.9; GLM-5.3 Flash's entire run is $0.083. Do not re-judge a run you have
already judged — `judge` is a separate command for that reason.

**Fresh input dominates GAIA's cost**, and the first call of every task is
uncached because each task is a new process. Within a process caching reaches
~95%; across processes Fireworks gives a new one nothing, while Claude Code's
account-wide cache hits 91% across its runs. That asymmetry flatters Claude
Code's token efficiency and is worth stating when reporting.

**Graders must not write into the agent's workdir.** A probe that writes
`data.txt` makes the judge blame the agent for a stray file. Grading runs after
the agent exits, and the diff is taken before the probes run — keep it that way
when adding a probe.

## Adding a model

1. Confirm the gateway can reach it: `gaia eval tasks run --suite everyday
   --tasks 21-qa --model <id> --no-judge`.
2. Add its published card to `metering.py::RATES`, keyed by the bare model
   name, or meter it with `--meter fireworks`.
3. Run it alongside at least one model you already have numbers for, so a
   harness change cannot be mistaken for a model difference.

## What good looks like

Numbers below are a snapshot from September 2026 — treat them as the shape to
expect, and re-measure rather than quoting them.

On the 14-task suite, Claude Code at Opus 5 scored 14/14 at quality 4.97,
truthfulness 5.00, 83 steps, 456 s. Against that ceiling GLM-5.3 Flash on GAIA
reached 14/14 at quality 4.96 for $0.09 of real Fireworks charges, and DeepSeek
V4.1 Flash 4.88 for $0.105. A model that costs more than the Sonnet reference
and scores below it is not a candidate, however good its public benchmarks
look.

Truthfulness is where the daylight is: the best legs score a clean 5.00, most
others sit at 4.86. That axis — never claiming what the tool record does not
support — is the one worth optimising next.
