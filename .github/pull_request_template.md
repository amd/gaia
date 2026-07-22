<!--
Thanks for contributing to GAIA! Every PR must reference a GitHub issue —
see CONTRIBUTING.md (https://github.com/amd/gaia/blob/main/CONTRIBUTING.md)
if you don't have one yet.
-->

## Summary

<!-- 1–2 sentences describing what this PR does, in plain English. -->

## Why

<!--
Why is this change being made? What problem does it solve, what was missing,
or what was painful? Reviewers need the motivation, not just the diff.
-->

## Linked issue

<!--
Required. Use a closing keyword so the issue auto-closes on merge.
Example:  Closes #123
If this PR only partially addresses an issue, use `Refs #123` instead.
-->

Closes #N <!-- replace N with the issue number, e.g. Closes #123 -->

## Changes

<!-- Bullet list of the meaningful changes a reviewer should know about. -->

-

## Test plan

<!--
How can a reviewer verify this works? Specific commands beat vague prose.
Mix automated and manual checks as needed.
-->

- [ ]

## Evidence

<!--
Real-world proof matched to the surface you changed — GAIA tests on the surface a user
actually touches. Green unit tests alone are not evidence. **Embed screenshots here in
the description** as `![caption](https://assets.amd-gaia.ai/testing/<pr>/<run>/shot.png)`
so they render on the PR — not a bare link, not comment-only. Mark a surface N/A (with a
reason) if the change doesn't touch it; delete the whole section only for pure
internal/docs/CI changes.
-->

- [ ] **Agent exposed in the Agent UI** (Chat, Email, …) — live browser (Playwright) **screenshot(s)**, before→after (required; text evidence does not substitute)
- [ ] **MCP tools / servers** — a live MCP client call + response (Agent UI MCP: `gaia mcp serve`)
- [ ] **CLI** — the `gaia <subcommand>` you ran and its actual output
- [ ] **HTTP API / REST** — the real request and the response (status + body)

## Checklist

- [ ] I have linked a GitHub issue above (`Closes #N` / `Fixes #N` / `Refs #N`).
- [ ] I have described **why** this change is being made, not just what changed.
- [ ] I have run linting and tests locally (`python util/lint.py --all`, `pytest tests/unit/`).
- [ ] I have attached **real-world evidence matched to the surface I changed** (see Evidence above), or marked each surface N/A.
- [ ] I have updated documentation if user-visible behavior changed (see [CONTRIBUTING.md](../CONTRIBUTING.md)).
