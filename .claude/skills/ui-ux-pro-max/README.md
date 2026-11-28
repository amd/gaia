# UI/UX Pro Max

A searchable set of UI and UX rules (styles, palettes, type pairings, accessibility,
motion, per-stack guidance) that Claude Code uses as a design review checklist.

**Made by [Next Level Builder](https://github.com/nextlevelbuilder)**, from
[nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill),
under the MIT License; see [LICENSE](LICENSE). All credit for the skill, its data and
its scripts goes to its authors. To use it outside GAIA, install it from that repo.

## This copy

- **Upstream commit:** `e8b065f0f3aaf2e680fd125f000e4f5e351ed108` (`.claude/skills/ui-ux-pro-max/`).
- **Left out:** upstream's own test suite, its scripts/tests folder. Using the skill doesn't need it.
- **Changed for GAIA's skill audit:**
  - `SKILL.md` declares `license`, the `community` tier, and the permissions its scripts
    use: they read the bundled data, write a design system only when asked to save one,
    and read `COLORTERM`.
  - One sentence about search results was reworded with the same meaning.

GAIA's own design language ([`docs/spec/gaia-design-language.mdx`](../../../docs/spec/gaia-design-language.mdx))
takes precedence over anything this skill suggests.

## Updating

Copy `.claude/skills/ui-ux-pro-max/` from a newer upstream commit, leave out
its scripts/tests folder, reapply the changes above, update the commit here, and run:

```bash
python -m pytest tests/unit/test_skills_audit_no_false_positives.py
```
