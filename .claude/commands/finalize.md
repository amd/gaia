Get the current feature branch ready for PR merge. Follow these phases exactly.

## Phase 1: Merge Latest Main

1. Capture current branch: `git branch --show-current`
2. Check for dirty working tree: `git status --porcelain`
   - If dirty: `git stash`
3. `git checkout main && git pull origin main`
4. `git checkout <branch> && git merge main`
5. If merge conflicts exist (git status shows conflict markers):
   - Report the conflicting files
   - **STOP** — do not proceed. The user must resolve conflicts manually.
6. If stashed: `git stash pop`

## Phase 2: Local PR Review (Replicating claude.yml)

### Generate diff artifacts
```bash
git diff origin/main...HEAD > pr-diff.txt
git diff --name-status origin/main...HEAD > pr-files.txt
```

### Self-review using the claude.yml checklist

Read `.github/workflows/claude.yml` lines 78–246 for the full review criteria. Apply that same checklist to the current branch changes.

**File reading strategy:**
- Read `pr-diff.txt` first — it shows ALL changes
- Read `pr-files.txt` to see which files changed
- For large files (>1000 lines), use Grep with context or Read with offset/limit — do NOT read the entire file
- Focus on reviewing CHANGED code, not entire files

**Apply all 7 review sections:**

1. **Code Quality & Patterns** — architecture consistency, pattern reuse, error handling, code style, CLAUDE.md compliance
2. **Security** — SQL injection, command injection, XSS, secrets exposure, path traversal, unsafe deserialization, resource cleanup
3. **Testing** — tests exist for new functionality, edge cases covered, test quality
4. **Documentation** — docs updated for new features/CLI commands/SDK changes
5. **Breaking Changes & Compatibility** — public API changes, backward compatibility
6. **Performance & Architecture** — N+1 queries, inefficient algorithms, unnecessary dependencies
7. **Commit Quality** — commit messages are clear and logical

**Classify all findings:**
- 🔴 **Critical** — Security issues, breaking changes, data loss risks
- 🟡 **Important** — Bugs, architectural concerns, missing tests
- 🟢 **Minor** — Style issues, optimizations, suggestions

**DO NOT review or flag:**
- Copyright headers (presence, absence, or year inconsistencies)
- SPDX license identifiers
- License-related boilerplate

### Fix findings
- Fix all 🔴 Critical and 🟡 Important issues immediately using Edit/Write tools
- Skip 🟢 Minor issues unless the fix is trivial (one-liner)
- For security issues: fix them directly

### Clean up diff artifacts
```bash
rm -f pr-diff.txt pr-files.txt
```

## Phase 3: Ralph Wiggum Loop

Loop until everything is green, **maximum 5 iterations**.

On each iteration:

### Step 1: Re-run local PR review
Repeat Phase 2 (generate diffs, review, fix, clean up). If no new 🔴/🟡 issues, proceed to Step 2.

### Step 2: Lint
```bash
python util/lint.py --all --fix
```
Check exit code. If lint still reports failures after `--fix`:
- Read the lint output carefully
- Manually fix remaining issues using Edit (common: import ordering, line length, f-string issues black can't auto-fix)
- Re-run `python util/lint.py --all` to verify clean

### Step 3: Run tests
```bash
python -m pytest tests/ -x --tb=short
```
- `-x` stops on first failure — analyze and fix before continuing
- Tests requiring external services (Lemonade server) skip automatically via pytest markers
- If tests fail: read the traceback, identify root cause, fix with Edit, then re-run

### Step 4: Evaluate
- If lint is clean AND tests pass AND no 🔴/🟡 issues in review → **exit loop, report success**
- If max iterations (5) reached → report remaining issues and stop

## Exit Report

Always end with:

```
## Finalize Implementation Report

**Branch:** <branch-name>
**Iterations:** <n>/5

### Lint
✅ Clean / ❌ <remaining issues>

### Tests
✅ All passed (<N> tests) / ❌ <failure summary>

### PR Review Verdict
✅ Approve / ✅ Approve with suggestions (minor only) / ❌ Request changes — <summary>

### Ready for PR
✅ Yes — branch is ready to open/update PR
❌ No — <list remaining blocking issues for user to resolve>
```

## Key Behaviors

- **Never commit** — only fix files; the user decides when to commit
- **Never skip the lint step** — lint failures will be caught by CI
- **Prefer Edit over Write** — surgical fixes only
- **Preserve existing tests** — if your fixes break tests, undo and rethink
- **If uncertain about a fix** — describe the issue and ask rather than guessing
