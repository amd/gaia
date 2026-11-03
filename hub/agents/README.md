# GAIA Hub Agents

Standalone agent packages for the GAIA Agent Hub. Each agent gets one directory
with a runtime subdir inside — `hub/agents/<id>/python/` for the Python wheel,
`hub/agents/<id>/npm/` for a JS/TS client, `hub/agents/<id>/cpp/` for a native
build. Each package depends on the published `amd-gaia` framework — third-party
contributors follow the exact same pattern AMD uses for its own agents.

## What ships here

Three packages ship: [`gaia/`](gaia/python/) (the flagship),
[`chat/`](chat/python/) (the base class it composes), and [`email/`](email/python/).
Per-task agents were collapsed into skills the flagship loads on demand, so a new
capability is a `SKILL.md` in [`hub/skills/`](../skills/), not a new package.

Custom agents are still supported — scaffold one with `gaia agent init` (below). The
smallest complete reference package, the shape `gaia agent init` produces, is
[`tests/fixtures/agents/hello-world/`](../../tests/fixtures/agents/hello-world/).

## Package layout

Every Python agent package has the same shape:

```
<id>/python/
├── gaia-agent.yaml          # Hub manifest (id, category, models, interfaces)
├── pyproject.toml           # gaia-agent-<id>, amd-gaia dep, gaia.agent entry point
├── gaia_agent_<id>/
│   ├── __init__.py          # build_registration() — advertises the agent
│   └── agent.py             # the Agent subclass
├── tests/
│   └── test_<id>.py         # unit tests (LLM mocked; unique basename per package)
└── README.md
```

## Install an agent for development

```bash
pip install -e hub/agents/<id>/python/          # registers via entry points
pytest hub/agents/<id>/python/tests/ -x
```

The GAIA registry discovers installed agents automatically through the
`gaia.agent` entry-point group — no hardcoded list.

## Build your own

```bash
gaia agent init my-agent --language python -o . --layout hub
```

That lands the package at `my-agent/python/`, matching every other agent here. See
[docs/guides/custom-agent.mdx](../../docs/guides/custom-agent.mdx) and the
[Agent Hub restructure spec](../../docs/spec/agent-hub-restructure.mdx).
