# Flagship baseline: Gemma-4-E4B on Strix Point

The scenario-eval baseline for `Gemma-4-E4B-it-GGUF`, the model every PC too small for
Qwen3.6-35B runs, measured on the Strix Point pool by
[`eval_flagship_gemma_stx.yml`](../../../../.github/workflows/eval_flagship_gemma_stx.yml).

**It has no scorecards yet.** Until it does, every category that workflow runs is
reported as NO BASELINE YET and is not compared. That is not a pass.

`meta.json` already names the model, the Claude driver and judge (`eval_model`) and the
pool. Those are what a run must match: the workflow refuses to start against a
directory whose `model` is a different model, and `gaia eval agent --compare` exits 3
for a scorecard measured with a different agent model or eval model.

## Capturing it

Only from a real run on the pool. Never write, estimate or edit a number here.

1. Dispatch the workflow on `main` with the categories to baseline:
   `gh workflow run eval_flagship_gemma_stx.yml --ref main -f categories="tool_selection context_retention error_recovery"`
2. Check the run measured everything: each lane's integrity gate is clean (no missing or
   unmeasured scenarios), and each scorecard's `config.agent_model` is
   `Gemma-4-E4B-it-GGUF` and `config.model` matches `eval_model` in `meta.json`.
3. Download each lane's `eval-gemma-stx-<lane>` artifact and copy
   `<category>/scorecard.json` here, unedited, as `scorecard_<category>.json`.
4. Fill in `meta.json` from that run: `commit`, `date`, `source_run` (the run URL),
   `iterations`, `categories`, `scenarios`, `passed`, `pass_rate`, and replace `purpose`.
5. Open a PR with the files and the run link. One run is one sample, so re-run any lone
   PASS-to-FAIL against it before calling it a regression.

To baseline more categories later, add their scorecards from a run of the same commit
range and update the counts. To change `eval_model`, re-capture every scorecard.

## The Strix Halo baseline is Gemma too

[`gaia-flagship/`](../gaia-flagship/) was also captured on Gemma-4-E4B, on Strix Halo,
because a fresh GAIA home resolves to Gemma. A 64 GB+ Halo user runs Qwen3.6-35B-A3B
instead, so that baseline measures a pairing few users have. Moving Halo to Qwen, after
v0.25.1 final:

1. Move `eval_flagship.yml`'s lane job onto `eval_scenario_lane.yml` with
   `agent-model: Qwen3.6-35B-A3B-GGUF` and a new `gaia-flagship-qwen-halo/` directory
   whose `meta.json` names that model.
2. Capture its scorecards from a real Halo run, as above.
3. Retire `gaia-flagship/` once nothing compares against it.
