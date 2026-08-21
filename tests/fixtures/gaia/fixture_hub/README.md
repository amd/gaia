# Fixture skill hub — a local, deterministic Agent Hub skills lane

Committed here: only **unsigned skill sources** (`sources/<name>/SKILL.md`,
verbatim copies of `hub/skills/<name>/SKILL.md`). The servable hub — catalog +
per-skill manifest + versioned `SKILL.md` + zip artifacts — is built **per
run** by `tests/fixtures/gaia/prepare_fixture_hub.py`, which signs bundles
with an **ephemeral** `eval-test-publisher` Ed25519 keypair. No private key is
ever committed; the throwaway key and its trust-store entry live only in the
run's `--skills-root`.

## Per-run setup

```bash
# 1. Build + sign into fixture_hub/_prepared (gitignored) and trust the key
#    in the agent-under-eval's skills root:
python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <agent skills root>

# 2. Serve it (ephemeral port; NEVER 4001/4200/8141/13305):
python tests/fixtures/gaia/serve_fixtures.py --dir tests/fixtures/gaia/fixture_hub/_prepared
# prints: SERVING http://127.0.0.1:<port>/

# 3. Point the agent at it:
GAIA_HUB_URL=http://127.0.0.1:<port>
```

`--skills-root` is required and must be the skills root the agent actually
uses (that is where `install_skill` reads `trusted-keys.json`). It is never
defaulted, so a developer's real `~/.gaia/skills` trust store can't pick up a
test key by accident.

## Contents and the tier behaviour scenarios can rely on (all verified)

| Skill | Version | Signed by default? | Install outcome |
|---|---|---|---|
| `data-explore` | 1.0.0 | yes (ephemeral key) | **installs cleanly at `community`** — no flags, no prompts. The clean hub-install target. |
| `github-triage` | 2.1.0 | **deliberately unsigned** | **refused** — unsigned collapses it to `experimental`, and its `shell:execute:gh` grant is above the `experimental` ceiling (which allows only `network:read`). The install-refusal scenario target; the error carries the `--allow-experimental` guidance chain. |

Also verified, for corpus planning: signing github-triage too
(`--unsigned ""`) makes it installable at `community`, but its
`shell:execute:gh` is a dangerous grant — the install **prompts** (declining
refuses; `assume_yes`/`--yes` installs). So `data-explore` remains the only
zero-interaction clean-install target.

Search behaviour: `search_skills("")` lists both; matching covers id, name,
description, and declared tool names (e.g. `query_data` → `data-explore`).

Rebuilding is cheap and idempotent — `prepare_fixture_hub.py` wipes and
recreates `--out`, and regenerates the keypair (`force=True`) each run.
