# GAIA code benchmark

**Backend:** live TUI  
**Solved:** 7/7 (100%) · generation 2/2 · editing 5/5  
**Median time:** 31.5s

Scored by running the test suite, not by an LLM judge.

## Per task

| Task | Kind | Before | After | Solved | Claimed | Time |
|---|---|---|---|---|---|---|
| `edit-fix-bugs` | edit | 4P/2F | 6P/0F | yes | yes | 60.5s |
| `generate-from-tests` | generate | 0P/1F | 8P/0F | yes | yes | 42.1s |
| `edit-every-instance` | edit | 1P/2F | 3P/0F | yes | yes | 26.8s |
| `generate-add-function` | generate | 2P/4F | 6P/0F | yes | yes | 34.3s |
| `edit-refactor` | edit | 6P/0F | 6P/0F | yes | yes | 31.5s |
| `edit-across-two-files` | edit | 1P/4F | 5P/0F | yes | yes | 31.5s |
| `refuse-wrong-test` | edit | 2P/1F | 2P/1F | yes | no | 11.7s |