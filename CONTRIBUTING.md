# Contributing to GAIA

Welcome! GAIA is AMD's open-source framework for running generative AI locally on AMD hardware. We appreciate your interest in making it better — bug reports, feature ideas, and pull requests are all valuable.

This guide covers the general contribution workflow. For documentation-specific contributions, see [`docs/reference/contributing-docs.mdx`](docs/reference/contributing-docs.mdx).

---

## Before you open a pull request — open an issue first

**Every pull request must reference a GitHub issue.** If an issue doesn't exist for what you're working on, please file one before you start coding using the [bug report](https://github.com/amd/gaia/issues/new?template=bug_report.yaml) or [feature request](https://github.com/amd/gaia/issues/new?template=feature_request.yaml) template.

Why we ask:

- It lets us discuss scope, design, and prior art **before** code is written, so reviews focus on implementation rather than direction.
- It avoids wasted effort if the change conflicts with planned work or the GAIA roadmap.
- It keeps the changelog and release notes usable — every shipped change can be traced to a tracked issue.

**Rare exceptions** (still helpful, but no issue required):

- Typo fixes or single-line documentation tweaks.
- Doc-only changes under ~10 lines.

If you're unsure whether your change qualifies, file an issue — it costs nothing and we can fast-track it.

---

## Filing an issue

Use the templates — they're short and we genuinely use every field:

- **[Bug report](https://github.com/amd/gaia/issues/new?template=bug_report.yaml)** — what broke, how to reproduce, what you expected.
- **[Feature request](https://github.com/amd/gaia/issues/new?template=feature_request.yaml)** — the **problem** you're trying to solve. We can usually find a good solution if the problem is clear; the reverse is harder.

Both templates have an optional **Acceptance criteria** field. If you can fill it in, please do — it makes the issue immediately ready to scope, and the resulting PR has a clear definition of done.

For security issues, **do not file a public issue** — open a private [security advisory](https://github.com/amd/gaia/security/advisories/new) instead.

---

## Submitting a pull request

1. **Claim the issue** — comment `.take` on it to assign it to yourself (works even without repo access), or comment normally so others know you're working on it.
2. **Branch off `main`** with a descriptive name (e.g. `fix/lemonade-startup-error`, `feat/jira-bulk-update`).
3. **Use the [PR template](.github/pull_request_template.md)** — every field matters. Don't delete sections; if a section doesn't apply, say so.
4. **Link the issue** with `Closes #N` (or `Fixes #N`, `Refs #N` for partial work). GitHub will auto-close the issue on merge.
5. **Run lint and tests locally** before pushing:
   ```bash
   python util/lint.py --all --fix
   pytest tests/unit/
   ```
6. **Keep the PR scope-clean** — one logical thread per PR. No drive-by formatting, no unrelated refactors. If you spot something else worth fixing, file a separate issue.

### What we expect in the PR description

The PR template asks for these because they make review faster and better:

- **Summary** — what changed, in plain English. *Not* a copy of the commit log.
- **Why** — the motivation. "Fixes a crash on startup" beats "Refactors `LemonadeClient`."
- **Linked issue** — `Closes #N` at the top.
- **Test plan** — specific commands or steps a reviewer can run. `pytest tests/unit/test_chat.py -k startup` is signal; "I tested it" is not.

A good Summary + Why example:

> **Summary:** Replace the silent fallback in `LemonadeClient` with a clear startup-time error.
> **Why:** When Lemonade Server isn't running, GAIA was returning empty responses with no indication of why. Users were filing bugs for the silent failure rather than the underlying setup issue.

---

## Code style and testing

Development setup, lint commands, and the test layout live in [`docs/reference/dev.mdx`](docs/reference/dev.mdx) — please follow that guide rather than relying on commands quoted here, since it's the canonical source. New features need tests; the [`tests/`](tests/) directory has examples for unit, integration, MCP, and CLI testing patterns.

---

## Documentation contributions

If you're adding or updating documentation, see the [Documentation Contribution Guide](docs/reference/contributing-docs.mdx) for which `docs/` directory to use (guides, playbooks, SDK reference, specifications, or reference). Documentation contributions still follow the issue-then-PR rule, except for the typo/small-edit exceptions noted above.

---

## Review timeline

We aim to acknowledge new issues and PRs within a few days. For pull requests, please stay responsive to review comments — if you need to step away, leave a quick comment so we know whether to wait or pick it up.

---

## Conduct

Be kind, be patient, and assume good intent. Most contributors are working on this in their own time — that includes maintainers reviewing your PR. We're all here to make GAIA better.

Thanks for contributing!
