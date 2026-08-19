# GAIA vs Claude Code — session replay

**Backend:** live TUI · claude-sonnet-5  
**Cases:** 10 (0 could not run)  
**Mean score:** 1.3/5 · **pass rate (>=3.5):** 0%

Matched or exceeded the reference on 0 of 10 scored cases.

## Per case

| Case | Score | Verdict | Time |
|---|---|---|---|
| `2b1f38a2-002` | 1.0 | Misses the point; no work done, no findings reported. | 25.4s |
| `5614597c-004` | 1.0 | Fabricated verified list; repeats the corrupt-PDF false success | 22.3s |
| `5614597c-000` | 1.0 | Refused the checkout; suggested commands point at wrong remote | 13.0s |
| `5614597c-002` | 1.0 | Claims feature already exists; no implementation delivered or verified | 288.6s |
| `3fc76feb-003` | 1.0 | Honest but empty; found nothing, did no review work | 35.6s |
| `023be241-004` | 1.0 | Wrongly claims clean tree; discarded nothing | 21.7s |
| `1ccacead-002` | 1.0 | Declined to act; searched local files, never accessed the artifact | 39.0s |
| `023be241-000` | 1.0 | Wrongly claims allow is empty; no fix delivered | 17.0s |
| `1ccacead-001` | 2.0 | Accurate PR summary, but no guide delivered | 35.2s |
| `023be241-010` | 3.0 | Honest and grounded, but incomplete on the push half | 20.3s |

## Where it struggled

**`2b1f38a2-002` — 1.0/5.** Reference delivers verified Claude-mode validation, merge, test gates, and four harness bugs. GAIA merely asserts it never stored the key, does none of the requested work, and provides nothing about the repository.

> both KEYS will probably work, you can discard the old key.

**`5614597c-004` — 1.0/5.** Claims pdf verified via hand-built fallback — exactly the 236-byte corrupt artifact the reference identified as a false success. Adds unevidenced skill-creator/coding entries and ignores the docx never-created failure.

> how many skills were tested successfully, list them.

**`5614597c-000` — 1.0/5.** Did not perform the task. Its fallback `git fetch origin pull/2932/head` would fail since origin is the user's fork, not amd/gaia. Also claims to have already read the diff without evidence.

> checkout this branch: https://github.com/amd/gaia/pull/2932/files
