# Community skills

This directory is the contribution entry point for community-authored skills. A
**skill** is a reusable capability any agent composes — see the
[publishing guide](https://amd-gaia.ai/docs/guides/hub-publishing) and the
[skill format spec](https://amd-gaia.ai/docs/spec/agent-skills).

[`hub/skills/`](../../hub/skills/) is the AMD starter pack — worked examples to
copy, **not** a place to submit. Community submissions live here, one directory
per skill.

## How to contribute

Skills are contributed **by pull request** — there is no self-serve publish API.
The Hub's publish endpoint is gated on a maintainer-held token secret, so a
contributor never handles one; `gaia skill publish` is what maintainers and CI
run *after* your PR merges.

1. **Add your skill under `skills/community/<your-skill-name>/`.** One directory
   per skill: at minimum a `SKILL.md`, plus `tools.py` and `scripts/` if it ships
   code.

2. **Audit it locally before opening the PR:**

   ```bash
   gaia skill audit ./skills/community/<your-skill-name>/
   ```

   Exit `0` (`ALLOW`) is what merges cleanly. This is the same engine CI runs, so
   a clean local run is the whole requirement.

3. **Open the PR.** The **Skill Audit (deterministic gate)** check is required:
   `BLOCK` (or an unparseable skill) fails it outright, and `REVIEW` holds the PR
   until a maintainer applies the `skill-audit-reviewed` label. Findings appear as
   counts and rule ids in a PR comment, with the detail in the repository's
   private Security tab.

4. **Publication happens after merge** — a maintainer publishes from AMD's
   credentials. You never touch a token.

## Merging is **not** a tier promotion

A skill's `security_tier` is earned by the audit verdict plus publisher signing —
`verified` additionally needs a human AMD audit that no automated scan grants. A
maintainer signing off on a `REVIEW` verdict unblocks your PR; it does not change
the verdict on record, and publishing still refuses a skill that has not earned
`ALLOW`. The gate owns the tier, not the reviewer.
