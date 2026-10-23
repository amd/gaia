# Flagship baseline: Qwen3.6-35B-A3B on Strix Halo

The scenario-eval baseline for `Qwen3.6-35B-A3B-GGUF`, the model `gaia init` installs on a
64 GB+ Strix Halo, measured on the Strix Halo pool by
[`eval_flagship.yml`](../../../../.github/workflows/eval_flagship.yml). Every tier on that
pool (RC smoke, full sweep, weekly backlog) scores this model unless a dispatch picks
another.

**It has no scorecards yet.** Until it does, every category is reported as NO
Qwen3.6-35B-A3B-GGUF BASELINE YET and is not compared. That is not a pass.

`meta.json` already names the model, the Claude driver and judge (`eval_model`) and the
pool. A run must match them: the workflow refuses a baseline directory whose `model` is
not the model under test, and `gaia eval agent --compare` exits 3 for a scorecard measured
with a different agent model or eval model. The Gemma baseline in
[`gaia-flagship/`](../gaia-flagship/) is never used for a Qwen run.

## Capturing it

Only from a real run on the pool. Never write, estimate or edit a number here.

1. Take a **full sweep** on Qwen: the nominated RC's tag run (`[full eval]` in its tag
   message), or `gh workflow run eval_flagship.yml --ref <tag> -f tier=full`. A smoke run
   covers 30 scenarios and is not a baseline.
2. Check it measured everything: each lane's integrity gate is clean, no lane ran out of
   Claude quota (zero-token errors fail the run), and each scorecard's
   `config.agent_model` is `Qwen3.6-35B-A3B-GGUF` and `config.model` matches `eval_model`.
3. Download each lane's `eval-<lane>` artifact and copy `<category>/scorecard.json` here,
   unedited, as `scorecard_<category>.json`.
4. Fill in `meta.json` from that run: `commit`, `date`, `source_run` (the run URL),
   `iterations`, `categories`, `scenarios`, `passed`, `pass_rate`, and replace `purpose`.
5. Open a PR with the files and the run link. One run is one sample, so re-run any lone
   PASS-to-FAIL against it before calling it a regression.

The full sweep leaves out the weekly backlog's `leaves_full` scenarios
([`eval/ci_tiers.json`](../../../../eval/ci_tiers.json)), so this baseline has none of
them. That is deliberate: they always fail, and the weekly run's trend artifact is their
measure. To change `eval_model`, re-capture every scorecard.
