---
description: Finalizes implementation by merging main, running the GAIA claude.yml code review (Opus), fixing issues, linting, and looping until tests pass.
---

You are finalizing the current branch's implementation. Work through the following steps in order. Be thorough and methodical — fix every issue you find before moving on.

---

## Step 1: Merge Latest from Main

1. Record the current branch: `git rev-parse --abbrev-ref HEAD`
2. Switch to main and pull: `git checkout main && git pull`
3. Switch back to the feature branch: `git checkout <branch>`
4. Merge main: `git merge main`
5. If merge conflicts exist, resolve them intelligently based on the intent of both sides. Prefer the feature branch changes unless main has clearly superseded them. Commit the resolution.

After merging, confirm you're back on the feature branch and working tree is clean.

---

## Step 2: Code Review (claude.yml Equivalent — Use Opus Agent)

This step replicates what the project's `.github/workflows/claude.yml` GitHub Action does when a PR is opened.

### 2a. Generate the diff

Run these commands to produce the review inputs:
```
git diff origin/main...HEAD > pr-diff.txt
git diff --name-status origin/main...HEAD > pr-files.txt
```

### 2b. Check for an existing PR and its review comments

Check if there is an open pull request for this branch:
```
gh pr list --head <branch> --json number,title,url
```

If a PR exists:
- Fetch existing Claude bot review comments: `gh pr view <number> --comments`
- Note any 🔴 Critical or 🟡 Important issues already flagged by the claude.yml action

### 2c. Launch the code-reviewer Opus agent

Use the **code-reviewer** sub-agent (which uses Claude Opus) to perform a full code review of `pr-diff.txt` and `pr-files.txt`. Instruct it to follow the same checklist as the `claude.yml` review:

**Review checklist (from claude.yml):**
- Code Quality & Patterns: architecture consistency, error handling, code style
- Security: SQL injection, command injection, XSS, secrets exposure, path traversal, unsafe deserialization, resource cleanup
- Testing: tests exist for new functionality, edge cases covered
- Documentation: docs/ updated for new features, CLI reference updated if needed
- Breaking Changes: public API compatibility
- Performance: N+1 queries, inefficient algorithms, unnecessary dependencies

**Severity classification:**
- 🔴 Critical — security issues, breaking changes, data loss risks
- 🟡 Important — bugs, architectural concerns, missing tests
- 🟢 Minor — style, optimizations (fix these too if easy)

**Do NOT flag:** Copyright headers, SPDX license identifiers.

### 2d. Fix all Critical and Important issues

Address every 🔴 Critical and 🟡 Important item found by the code-reviewer agent. Also fix 🟢 Minor issues when straightforward. After fixing, do a quick re-read of changed files to verify correctness.

---

## Step 3: The Ralph Wiggum Loop

Repeat this loop until **all three conditions pass**:
- ✅ Lint passes with no errors
- ✅ Code review finds no Critical or Important issues
- ✅ Unit tests pass

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

### 3d. Loop Control

After completing 3a–3c:
- If **any step failed**, return to 3a and repeat
- If **all steps passed**, exit the loop

---

## Completion

When the loop exits with everything passing, report:

```
✅ FINALIZE COMPLETE

Branch: <branch-name>
Merged from: main

Code Review: No Critical or Important issues remaining
Lint: Passing
Tests: All unit tests passing

Ready for PR review / merge.
```

If a PR already exists, note its URL so the user can submit it for final merge.
