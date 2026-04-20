# GAIA-Internal-20 Fixtures

Each fixture is a small ``git apply``-able patch that seeds a bug, flaky
test, or starting artifact into the sandbox **before** the agent runs.
The suite scorer calls ``gaia.eval.suites.gaia_internal_20.scorer.apply_fixture``
to lay them down.

## Inventory (Phase 2)

| Task | Fixture file | Status | Seeds |
|------|--------------|--------|-------|
| T07  | `T07.patch`  | Phase 2 stub | failing test + off-by-one helper |
| T09  | `T09.patch`  | Phase 2 stub | over-broad deny rule in `coder/tools/cli.py` |
| T14  | `T14.patch`  | Phase 2 stub | path-traversal vuln in `coder/tools/file.py` |
| T17  | `T17.patch`  | Phase 2 stub | flaky `test_cache_ttl` |
| T18  | `T18.patch`  | Phase 2 stub | `except Exception: pass` in `coder/introspect/` |
| T20  | `T20.patch`  | Phase 2 stub | mock history for yesterday's work |

The Phase 2 stubs are **empty valid patches** — they satisfy the
harness plumbing (scorer can call ``git apply`` without failing) but
do not yet contain the real seeded bugs. Real seeded bugs land with
the Phase 3 agent-loop work, when the agent becomes capable of
actually *detecting* and *fixing* them. The task front-matter is
authoritative and won't need to change when the fixtures gain real
content.

## Authoring new fixtures

1. Make your changes in a clean sandbox copy.
2. `git diff` relative to `coder` HEAD.
3. Write the diff to `T##.patch` with a header:

   ```diff
   # Seeds: <one-line description>
   # Applied by gaia.eval.suites.gaia_internal_20.scorer.apply_fixture
   ```

4. Verify with `git apply --check fixtures/T##.patch` from a clean
   worktree.

Keep fixtures **small** (< 50 LoC) — they exist to exercise one bug,
not ship product features.
