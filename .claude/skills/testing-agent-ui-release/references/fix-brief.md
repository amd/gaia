# Fix-session brief

One session per issue (`start_session`, fresh context, its own worktree), started only
after the person's go-ahead to push and open PRs. Fill in the brackets. The brief is
all the session knows, so include the repro and the evidence path.

```text
Fix amd/gaia issue #<N>: <title>.

What a person sees: <one sentence>.
Repro (desktop app, <build>): <numbered steps>. Expected <x>, actual <y>.
Evidence: <absolute path to screenshots>. Backend trace: <app_state.py last output, trimmed>.
Likely area: <files or component, if known; say "unknown" otherwise>.

Done means:
- A test that fails before the fix and passes after it (unit, or test:rtl / vitest for the UI).
- The fix, with CLAUDE.md rules: no silent fallbacks, one-line comments at most, no unrelated changes.
- Lint and the affected tests pass (`python util/lint.py --all`, the pytest or npm test paths you touched).
- If the change is visible in the Agent UI, follow the reviewing-agent-ui skill and say which screens you looked at.
- If it touches prompts, tools or the agent loop, name the eval categories to watch in the PR.
- A commit on a branch named fix/<N>-<slug>, pushed to a remote you can push to (your fork if you can't push to amd/gaia), and a PR whose body says "Closes #<N>".
- Report back: branch, PR URL, the test you added, and anything you could not verify.

Do not: edit the release test report, touch other issues, push to main, change the person's own GAIA install,
or add any AI attribution (no Co-Authored-By trailer, no "Generated with" line) to commits or the PR.
```
