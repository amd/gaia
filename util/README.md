<!--
Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Repo tooling

Scripts the repo runs on itself: lint, CI policy, releases and evals. Nothing here ships
to users. Each script's own docstring says what it does and how to call it
(`python util/<script>.py --help`).

| Group | Scripts | Used by |
|---|---|---|
| Lint and code checks | `lint.py` (black, isort, pylint, …), `check_added_lines.py`, `check_agent_conventions.py`, `check_tool_descriptions.py`, `check_doc_code.py`, `check_doc_links.py`, `check_doc_versions.py`, `check_licenses.py`, `check_security_gates.py` | `lint.yml`, and before every commit |
| CI policy | `check_ci_path_coverage.py`, `check_test_lane_coverage.py`, `check_module_skips.py`, `check_workflow_triggers.py`, `check_workflow_ancestor_skip.py`, `check_dependabot.py`, `select_tests.py` | Unit and lint workflows |
| Releases | `release_tag.py`, `release_versions.py`, `release_train.py`, `release_promote.py`, `check_release_ci.py`, `check_release_assets.py`, `check_flagship_release.py`, `gaiapy_rc_assets.py`, `gaiacpp_release_assets.py`, `validate_release_notes.py` | `publish.yml`, the `gaia-release` skill |
| Evals | `run_eval_lane.py`, `eval_tiers.py`, `eval_lane_matrix.py`, `eval_tag_gate.py`, `eval_failure_notice.py`, `eval_backlog_trend.py` | `eval_flagship.yml` |
| Reviews and triage | `check_review_verdict.py`, `triage_scan.py`, `approve_fork_runs.py`, `select_claude_account.py` | Claude review workflows, the `contributor-triage` skill |
| Hand-run tools | `compare_local_models.py`, `perf_turns.py`, `measure_first_turn.py`, `bench_browser_pane.py`, `lemonade_restart_harness.py`, `tui_driver.py`, `machine_turn.py`, `cvss4.py` | Developers, on a real PC |

`ci_path_allowlist.yml` and `test_lane_allowlist.yml` are the reviewed exceptions the CI
policy checks read.
