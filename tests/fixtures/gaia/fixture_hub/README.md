# Fixture skill hub — a local, deterministic Agent Hub skills lane

A servable copy of the hub layout `gaia.skills.hub` fetches (catalog +
per-skill manifest + versioned `SKILL.md` + zip artifact), so
`search_skill_hub` / `install_skill` eval scenarios run with **zero network**.

## Serving it

```bash
python tests/fixtures/gaia/serve_fixtures.py --port 8765
# then, in the agent's environment:
GAIA_HUB_URL=http://127.0.0.1:8765/fixture_hub
```

(`GAIA_HUB_URL` may carry a path segment; every `gaia.skills.hub` /
`gaia.hub.catalog` URL is built by suffixing it. Serving with
`--dir tests/fixtures/gaia/fixture_hub` and no path suffix works too.)

## Contents

| Skill | Version | Claimed tier | Permissions |
|---|---|---|---|
| `github-triage` | 2.1.0 | community | `shell:execute:gh` |
| `data-explore` | 1.0.0 | community | (none) |

`SKILL.md` files are verbatim copies of `hub/skills/<name>/SKILL.md`. Rebuild
after a starter-skill change with
`python tests/fixtures/gaia/fixture_hub/_build_fixture_hub.py` — it re-zips
deterministically and re-stamps the sha256s; never edit the manifests by hand.

## Tier behaviour scenarios can rely on (verified against this fixture)

The zips are **unsigned**, so `attested_tier` is `experimental` and the
community claim collapses to `experimental` at install
(`effective_tier = min(claimed, attested)`):

- `install_skill("data-explore")` → **refused** (`SkillInstallError`: needs
  `--allow-experimental`).
- `install_skill("data-explore", allow_experimental=True)` → **installs** at
  tier `experimental` (re-stamped from the community claim; sha256 verified,
  lock entry recorded).
- `install_skill("github-triage", allow_experimental=True)` → **refused**
  (`SkillPermissionError`: `shell:execute:gh` is above the `experimental`
  ceiling, which allows only `network:read`). This is the canonical
  "unsigned skill cannot carry a binary grant" refusal scenario — installing
  github-triage for use in scenarios must go through the signed/real hub or
  the documented `~/.gaia/skills/` copy method, not this fixture.

Search behaviour: `search_skills("")` lists both; matching covers id, name,
description, and declared tool names (e.g. `query_data` → `data-explore`).
