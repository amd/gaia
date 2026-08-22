# Fixture skill hub — a local, deterministic Agent Hub skills lane

Committed here: only **unsigned skill sources** (`sources/<name>/…`). The
servable hub — catalog + per-skill manifest + versioned `SKILL.md` + zip
artifacts — is built **per run** by `tests/fixtures/gaia/prepare_fixture_hub.py`,
which signs bundles with an **ephemeral** `eval-test-publisher` Ed25519
keypair. No private key is ever committed; the throwaway key and its
trust-store entry live only in the run's `--skills-root`.

## Per-run setup

```bash
# 1. Build + sign into fixture_hub/_prepared (gitignored) and trust the key
#    in the agent-under-eval's skills root:
python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <agent skills root>

# 2. Serve the routed fixture layout (eval runs use port 8765; NEVER 4001):
python tests/fixtures/gaia/serve_fixtures.py --port 8765

# 3. Point the agent at it:
GAIA_HUB_URL=http://127.0.0.1:8765/fixture_hub
```

`--skills-root` is required and must be the skills root the agent actually
uses (that is where `install_skill` reads `trusted-keys.json`). It is never
defaulted, so a developer's real `~/.gaia/skills` trust store can't pick up a
test key by accident.

## Catalog — exactly three skills (a scenario asserts the exact catalog)

Per the corpus contract (`eval/scenarios/GAIA_FIXTURE_VALUES.md`):

| skill | version | tier | signed | role in scenarios |
|---|---|---|---|---|
| `github-triage` | 2.1.0 | community | yes | searched / pre-seeded only — **never installed by scenarios** (its `shell:execute:gh` is a dangerous grant, so installing prompts even signed) |
| `rss-digest` | 1.0.0 | community | yes | **the clean-install target** — `network:read` only, installs at `community` with zero flags and zero prompts; the install-success scenario downloads THIS |
| `experimental-notes` | 0.0.1 | experimental | **no** | **the install-refusal target** — unsigned, refused with the `--allow-experimental` guidance |

`github-triage` and `rss-digest` sources are verbatim copies of
`hub/skills/<name>/` (rss-digest includes its `tools.py`);
`experimental-notes` is a minimal instruction-only skill that exists solely
for the refusal scenario.

Search behaviour: `search_skills("")` lists exactly the three; matching covers
id, name, description, and declared tool names (e.g. `fetch_rss` →
`rss-digest`).

Rebuilding is cheap and idempotent — `prepare_fixture_hub.py` wipes and
recreates `--out`, and regenerates the keypair (`force=True`) each run.
