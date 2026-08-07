---
description: Finalizes the current branch — rebases onto main, runs the claude.yml-equivalent code review (Opus), then loops on lint + review + tests (+ agent eval for LLM-affecting changes) until all gates pass. Stops short of pushing.
---

You are finalizing the current branch's implementation. Work through the following steps in order. Be thorough and methodical — fix every issue you find before moving on.

---

## Step 1: Rebase onto Latest Main

1. Record the current branch: `git rev-parse --abbrev-ref HEAD`
2. Verify the working tree is clean: `git status --porcelain`
   - If dirty: stop and tell the user to commit or stash their changes first.
3. Fetch latest: `git fetch origin main`
4. Rebase onto main: `git rebase origin/main`
   - If conflicts arise, resolve them per-commit. Prefer the feature branch intent unless main has clearly superseded it. After resolving each conflict: `git add <files>` then `git rebase --continue`.
   - If the rebase becomes intractable, run `git rebase --abort` and fall back to `git merge origin/main` with a warning to the user.
5. Confirm success: `git log --oneline origin/main..HEAD` should show only feature commits, no merge commits.
6. **Do NOT push.** CLAUDE.md prohibits pushing and force-pushing without explicit user
   instruction, and a post-rebase push is always a force-push. Report the branch as
   *rebased and ready to push*, and give the user the exact command to run:
   - No remote tracking branch yet → `git push -u origin <branch>`
   - Remote branch exists → `git push --force-with-lease`

   Only run it yourself if the user asks in this session, in so many words.

---

## Step 2: Code Review (claude.yml Equivalent — Use Opus Agent)

This step replicates what the project's `.github/workflows/claude.yml` GitHub Action does when a PR is opened.

### 2a. Generate the diff

Write the review inputs to a temp dir — never into the repo root, where they'd show up as
untracked clutter or get committed by accident:
```
REVIEW_DIR=$(mktemp -d)
git diff origin/main...HEAD > "$REVIEW_DIR/pr-diff.txt"
git diff --name-status origin/main...HEAD > "$REVIEW_DIR/pr-files.txt"
echo "$REVIEW_DIR"
```
Delete the directory when Step 3 exits.

### 2b. Check for an existing PR and its review comments

Check if there is an open pull request for this branch:
```
gh pr list --head <branch> --json number,title,url
```

If a PR exists:
- Fetch existing Claude bot review comments: `gh pr view <number> --comments`
- Note any 🔴 Critical or 🟡 Important issues already flagged by the claude.yml action

### 2c. Launch the code-reviewer Opus agent

Use the **code-reviewer** sub-agent (which uses Claude Opus) on the two files from 2a.

**Instruct it to follow [`REVIEW.md`](../../REVIEW.md) — read it, don't paraphrase it.**
That file is the single source of truth for the review dimensions, severity ladder, nit
budget, skip list, and length caps; it is what the `claude.yml` action runs against. Any
checklist restated here would be a copy that drifts out of sync with the real reviewer.

### 2d. Fix what the review found

Fix every 🔴 Critical and 🟡 Important item. Handle 🟢 nits per REVIEW.md's cap rather
than sweeping up every one. After fixing, re-read the changed files to verify correctness.

---

## Step 3: The Ralph Wiggum Loop

Repeat this loop until **all four conditions pass**:
- ✅ Lint passes with no errors
- ✅ Code review finds no Critical or Important issues
- ✅ Unit tests pass
- ✅ Agent eval matches baseline — **only if the diff touches an LLM-affecting surface** (3d)

### 3a. Lint

Run the linter with auto-fix:
```
python util/lint.py --all --fix
```

Check the output. If the linter reports issues it could not auto-fix, fix them manually. Common issues:
- Import ordering (isort violations) — reorder imports
- Formatting (black violations) — reformat the affected code
- Trailing whitespace, missing newlines at EOF

Re-run lint to confirm it passes cleanly before continuing.

### 3b. Re-run Code Review (Opus agent)

Launch the **code-reviewer** agent again on the current diff (`git diff origin/main...HEAD`) to check if your fixes introduced any new issues or if any Critical/Important items remain unresolved.

Fix any newly found Critical or Important issues.

### 3c. Run Unit Tests

```
python -m pytest tests/unit/ -x --tb=short
```

The `-x` flag stops at the first failure. Analyze failures:
- Read the full traceback
- Identify the root cause (changed interface, broken import, logic error, etc.)
- Fix the underlying issue — do NOT skip or mock away real failures
- Re-run tests to confirm the fix

If all unit tests pass, optionally run the full test suite:
```
python -m pytest tests/ -x --tb=short
```
(Skip integration tests that require external services like Lemonade if they are not running)

### 3d. Agent Eval — conditional, but MANDATORY when it applies

Unit tests pass on prompt regressions. Evals are the only gate that catches them, and
CLAUDE.md makes this **required, not optional** — skipping it is how #1030 shipped.

**First, decide whether it applies.** Check the changed files for an LLM-affecting surface:
```
git diff --name-only origin/main...HEAD
```
It applies if the diff touches any of: a `_get_system_prompt()` or prompt fragment, the
base agent's prompt assembly, `@tool` registration or tool docstrings (the model sees the
docstring as its schema), error classification, the default model or `is_tool_calling_model`
mapping, or tool-call response parsing. **If none are touched, skip 3d and say so** — don't
run an eval on a docs-only or CI-only change.

**If it applies, run it** (backend must be up first):
```
python -m gaia.ui.server --port 4200 --host 127.0.0.1     # terminal 1
gaia eval agent --category <category> --agent-type <type>  # terminal 2 — prints the run dir
gaia eval agent --compare <matching-baseline>/scorecard_<category>.json <run-dir>/scorecard.json
```
`--compare` only diffs two scorecards; it does not run anything. Pick the baseline under
`tests/fixtures/eval_baselines/` that matches your model — don't `ls -t` for the newest,
a fresh clone stamps them all with the checkout time.

⚠️ **Run evals SERIALLY — never two at once.** Concurrent runs race-evict each other's
models on the shared Lemonade backend and produce garbage failures (`ctx_size` errors,
spurious `INFRA_ERROR`). Before starting, confirm nothing else is running:
```
ps aux | grep "gaia eval" | grep -v grep | wc -l    # must print 0
```

A regression means fix the prompt and re-run in this same session. If the drop is
intentional, regenerate with `--save-baseline` and flag it explicitly for the user — never
silently.

### 3e. Loop Control

After completing 3a–3d:
- If **any step failed**, return to 3a and repeat
- If **all steps passed**, exit the loop and delete `$REVIEW_DIR`

---

## Completion

Report per [CLAUDE.md → How You Communicate](../../CLAUDE.md#how-you-communicate): open
with one plain sentence on where the branch stands and what the user does next, then the
gate results underneath. Don't narrate the loop.

```
Branch <name> is rebased on main and green — ready for you to push.

  git push --force-with-lease

- Lint: passing
- Code review: no Critical or Important issues
- Tests: <N> unit tests passing
- Eval: <not applicable — no LLM-affecting surface touched | rag_quality matches baseline>
```

Name the biggest thing you had to fix during the loop if it changes what the user should
look at; otherwise leave it out. If a PR already exists, include its URL. Remember you have
**not** pushed — the branch is local until the user runs the command.
