---
type: plan
source-issue: 1293
repo: amd/gaia
title: "Agent UI first-boot fails: interactive Delete/re-download prompt dead-ends non-interactive backend"
created: 2026-05-29
status: in-progress
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 2
estimated_files_changed: 2
test_command: "PYTHONPATH=\"$PWD/src\" /Users/tomasz/src/amd/gaia/.venv/bin/python -m pytest tests/unit/test_lemonade_model_loading.py -xvs"
build_command: ""
lint_command: "/Users/tomasz/src/amd/gaia/.venv/bin/python util/lint.py --black --isort"
branch: tmi/issue-1293-noninteractive-boot
reflection_iterations: 0
agents_used: [planning, execution, validation]
---

# Issue #1293 — Non-interactive boot auto-heal

Stacked on `tmi/issue-1294-corrupt-classification` (parent commit `cccc34ff`). #1294
already narrowed `_is_corrupt_download_error` (removed the bare "llama-server failed to
start" signal); this branch does NOT touch that function or its tests.

## Files
- `src/gaia/llm/lemonade_client.py` — `_prompt_user_for_delete` guard + `load_model`
  corrupt-download branch honoring `prompt`.
- `tests/unit/test_lemonade_model_loading.py` — new TDD coverage (NOT the
  error_classification file).

## Bug
On a fresh Agent UI install the corrupt-download repair path in `load_model` fires
interactive `[y/N]` prompts inside the FastAPI lifespan threadpool (no TTY). `input()`
raises `EOFError` / hangs → boot-init job fails. Two mechanisms:
1. `_prompt_user_for_delete` lacks the `sys.stdin.isatty()/sys.stdout.isatty()` guard its
   siblings (`_prompt_user_for_download`, `_prompt_user_for_repair`) both have.
2. The corrupt-download branch calls `_prompt_user_for_repair` / `_prompt_user_for_delete`
   unconditionally, ignoring the `prompt` argument that boot callers pass as `prompt=False`
   (`lemonade_manager._try_preload_with_ctx`, `ui/server.py:_load_model`).

## Recovery-policy design (UX-first: silent success, loud only when unrecoverable)
- When `prompt=False` OR stdin/stdout not a TTY: NEVER call `input()` in any branch.
- `_prompt_user_for_delete` gets the same non-interactive guard → returns the proceed
  default under non-TTY so auto-heal can continue.
- Corrupt-download branch HONORS `prompt`: with `prompt=False`, skip the prompts and
  auto-proceed (resume → if that fails, ONE delete+redownload). Bounded to a single
  delete+redownload; no loops.
- Surface recovery PROGRESS at INFO (percent from `pull_model_stream` events) so the
  backend log (tailed by the UI) shows movement and boot doesn't look frozen. The
  corrupt-detected / repairing "why" detail logs at DEBUG.
- Unrecoverable after the single delete+redownload → one loud actionable
  `LemonadeClientError` (what failed / what to do — UI Force-redownload or manual recovery /
  where to look — Lemonade server.log). No EOFError, no hang, no silent swallow.
- Interactive TTY (`prompt=True` + real TTY) still prompts as today.

## Acceptance criteria
1. No `load_model` branch calls `input()` when `prompt=False` OR non-TTY.
2. `_prompt_user_for_delete` has the isatty guard like its two siblings.
3. Corrupt-download repair/delete branch honors the `prompt` argument.
4. Non-interactive corrupt model → automatic recovery, bounded to ONE delete+redownload,
   no prompt.
5. Recovery surfaces progress (INFO from the pull stream); repair detail at DEBUG.
6. Unrecoverable → a single loud actionable `LemonadeClientError`. No EOFError, no hang.
7. Interactive prompting still works when `prompt=True` and a TTY is present.
