---
type: plan
source-issue: 1294
repo: amd/gaia
title: "Lemonade: _is_corrupt_download_error misclassifies generic llama-server failed to start as corruption"
created: 2026-05-29
status: in-progress
work_type: code-refactor
complexity: trivial
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 2
test_command: "PYTHONPATH=\"$PWD/src\" /Users/tomasz/src/amd/gaia/.venv/bin/python -m pytest tests/unit/test_lemonade_error_classification.py -xvs"
build_command: ""
lint_command: "/Users/tomasz/src/amd/gaia/.venv/bin/python util/lint.py --black --isort"
branch: tmi/issue-1294-corrupt-classification
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue #1294 — `_is_corrupt_download_error` misclassifies generic `llama-server failed to start`

## Problem
`LemonadeClient._is_corrupt_download_error` (`src/gaia/llm/lemonade_client.py`, ~1225-1248)
treats the generic substring `"llama-server failed to start"` as evidence of a corrupt /
incomplete model download. Lemonade raises that string for many NON-corruption failures
(resource limits, ctx_size issues, GPU/backend startup, port conflicts). Misclassifying
routes ordinary load failures into a delete-and-redownload path (default model ~25GB) and
dead-ends first-boot.

The real-world payload was `{"code":"model_load_error","type":"model_load_error",
"message":"...llama-server failed to start"}` — `code`/`type` is `model_load_error`,
which is NOT a corruption signal.

## Fix (surgical)
Remove `"llama-server failed to start"` from the unconditional corruption phrase list in
`_is_corrupt_download_error`. Treat that string as corruption ONLY when a specific corruption
phrase is ALSO present (corroboration); otherwise return `False`. Keep the five existing
specific corruption phrases unconditional:
- `"download validation failed"`
- `"files are incomplete"`
- `"files are missing"`
- `"incomplete or missing"`
- `"corrupted download"`

This makes a bare `llama-server failed to start` load failure fall through to `load_model`'s
existing non-corrupt branch, which raises an actionable `LemonadeClientError` and does NOT
enter the delete + `pull_model_stream` repair path.

## Files to change
1. `src/gaia/llm/lemonade_client.py` — `_is_corrupt_download_error` only. Do NOT touch the
   prompt helpers (`_prompt_user_for_delete` / `_prompt_user_for_repair`) or `load_model`'s
   corrupt branch (owned by stacked issue #1293).
2. `tests/unit/test_lemonade_error_classification.py` — APPEND new test classes (file already
   exists with #1030 regression tests; preserve them). Do NOT touch
   `tests/unit/test_lemonade_model_loading.py` (owned by #1293).

## Test approach (TDD — red first)
Append to `tests/unit/test_lemonade_error_classification.py`:
- Parametrized `_is_corrupt_download_error`: each of the 5 specific phrases -> True; bare
  `"llama-server failed to start"` -> False; that string PLUS a corruption phrase -> True;
  the real `model_load_error` structured payload -> False.
- `load_model` test: mock `_send_request` to raise the bare `llama-server failed to start`
  error; assert `delete_model` and `pull_model_stream` are NOT called and an actionable
  `LemonadeClientError` is raised.
- `load_model` test: a specific corruption error DOES enter the repair path (resume via
  `pull_model_stream`).

## Acceptance criteria
1. `_is_corrupt_download_error("...llama-server failed to start...")` -> False unless a
   specific corruption signal is also present.
2. The five existing specific phrases continue to return True (no regression).
3. A bare `llama-server failed to start` load failure makes `load_model` raise an actionable
   `LemonadeClientError` and does NOT enter delete+redownload.
4. When corruption IS correctly detected (a specific phrase), the existing repair flow runs.
