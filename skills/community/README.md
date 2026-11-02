# Community skills

Community skills now go straight into [`hub/skills/`](../../hub/skills/); this
directory holds only this pointer. A **skill** is a reusable capability any agent
composes: see the [skill format spec](https://amd-gaia.ai/docs/spec/agent-skills).

## How to contribute

Follow [Build and Publish a Skill](https://amd-gaia.ai/docs/guides/building-skills).
In short:

1. **Add your skill under `hub/skills/<your-skill-name>/`** in a fork. At minimum a
   `SKILL.md`, plus `tools.py` and `scripts/` if it ships code.
2. **Audit it locally:** `gaia skill audit ./hub/skills/<your-skill-name>/`. Exit `0`
   (`ALLOW`) is what merges and publishes cleanly.
3. **Open a pull request.** Skills are contributed by pull request only; there is
   no self-serve publish API, and you never handle a signing key or hub token.
   The **Skill Audit (deterministic gate)** check is required: `BLOCK` fails it,
   and `REVIEW` holds the PR until a maintainer applies the `skill-audit-reviewed`
   label.
4. **After it merges,** the next final GAIA release publishes it to the hub,
   signed by AMD.

## Merging is **not** a tier promotion

A skill's `security_tier` is earned by the audit verdict plus publisher signing.
A maintainer signing off on a `REVIEW` verdict unblocks your PR; it does not
change the verdict on record, and publishing still refuses a skill that has not
earned `ALLOW`.
