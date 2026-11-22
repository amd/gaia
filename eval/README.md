<!--
Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Eval data

What `gaia eval` runs against. The framework that runs it is
[`src/gaia/eval/`](../src/gaia/eval/); how to run it is in
[`docs/reference/eval.mdx`](../docs/reference/eval.mdx).

| Path | What it holds |
|---|---|
| `scenarios/<category>/` | The agent eval scenarios `gaia eval agent --category` runs, one YAML per scenario |
| `ci_lanes.json` | Which categories each CI lane runs; `util/eval_lane_matrix.py` checks it |
| `ci_tiers.json` | The smoke and full tiers, and which committed baseline each model compares against |
| `corpus/` | Documents the scenarios read, with a manifest of what each contains |
| `prompts/` | The simulator, judge and auto-fixer prompts |
| `retrieval/` | Datasets and sources for `gaia eval retrieval` |
| `tasks/` | Outcome-scored tasks for `gaia eval tasks`, and the SWE-bench subsets |
| `analysis/` | Snapshots used in eval write-ups |
| `results/` | Local run output. Its `baseline.json` is a small local baseline from April 2026, not a release baseline |

Baselines are not here. The committed ones are under
[`tests/fixtures/eval_baselines/`](../tests/fixtures/eval_baselines/), copied unedited from
CI runs on the Strix Halo pool.
