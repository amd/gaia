# Skill fixtures

Sample `SKILL.md` folders used by `tests/unit/test_skills_*.py` (issue #888).

| Fixture | What it proves |
|---|---|
| `bare-standard/` | A bare agentskills.io skill (only `name` + `description`) loads instruction-only with conservative defaults. |
| `web-search/` | A GAIA tool skill: `metadata.gaia.tools` matching a real `tools.py`, plus a connector-bridged `network:read` permission. |
| `triage-support-ticket/` | A recipe skill that consumes registry tools via `tools_required` (the #887/#1451 contract). |
| `tool-mismatch/` | Declares a tool `tools.py` does not register — must fail loudly with no partial load. |
| `local-capability/` | Declares `filesystem:write` — must be refused as deferred to a later phase. |
| `incident-review/` | A Claude Code skill (with `allowed-tools` / `compatibility`) copied into a `.claude/skills/` root to prove read-only import. |

Tests copy these into a `tmp_path` root rather than reading them in place, so no
test ever touches the developer's real `~/.gaia/skills/`.
