# Shared digest test vectors (issue #2468)

The audit report's digests are a **byte-for-byte cross-language contract**: the
Python engine (`src/gaia/skills/audit/findings.py`) computes them, and the hub
Worker (`workers/agent-hub/src/audit.ts`) recomputes `manifest_digest` to reject
a report replayed against a different `SKILL.md`.

Two implementations of the same hash is exactly the situation where each side
gets verified only against its own mock — proving the function was called, never
that the two interoperate. So both suites assert against **these** vectors:

| Suite | File |
|---|---|
| Python | `tests/unit/test_skills_audit_digest_vectors.py` |
| Worker | `workers/agent-hub/test/audit-digest-vectors.test.ts` |

## Files

- `vectors.json` — the expected digests. **Do not regenerate this to make a test
  pass.** If a digest changes, the wire format changed: bump `AUDIT_ENGINE`, and
  update both implementations together.
- `tree/` — a small skill tree covering the cases that distinguish the algorithm:
  nested `scripts/`, a `__pycache__` that must be ignored, and CRLF content.

## The algorithms

`manifest_digest(text)` — `sha256` over the `SKILL.md` text with CRLF and CR
normalized to LF, so a Windows checkout matches the LF bytes the author audited.

`content_digest(dir)` — `sha256` over one record per file, files sorted by
relative POSIX path. Each record is:

```
<relative posix path> NUL <byte length as ASCII decimal> NUL <raw bytes>
```

The path is included so a rename changes the digest; the length keeps the
concatenation unambiguous. `__pycache__`, `.git`, and compiled artifacts are
excluded — a build cache appearing must not invalidate a valid report. Unlike
`manifest_digest`, file bytes are hashed **raw**, with no line-ending
normalization, because it covers arbitrary content and not just text.
