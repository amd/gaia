# GAIA code benchmark

**Backend:** live TUI  
**Solved:** 3/4 (75%) · generation 1/2 · editing 2/2  
**Median time:** 18.6s

Scored by running the test suite, not by an LLM judge.

**1 task(s) claimed success while the suite disagreed.** That is the failure mode worth fixing first.

## Per task

| Task | Kind | Before | After | Solved | Claimed | Time |
|---|---|---|---|---|---|---|
| `edit-fix-bugs` | edit | 4P/2F | 6P/0F | yes | yes | 52.8s |
| `generate-from-tests` | generate | 0P/1F | 8P/0F | yes | yes | 20.8s |
| `edit-every-instance` | edit | 1P/2F | 3P/0F | yes | no | 16.3s |
| `generate-add-function` | generate | 2P/4F | 2P/4F | NO | yes | 2.6s |

## Where it failed

**`generate-add-function`** — Add a new function to an existing module without disturbing it.
4 test(s) still failing — **and it reported success anyway.**
