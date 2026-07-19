# GAIA Hub Agents

Standalone agent packages for the GAIA Agent Hub. Each agent gets one directory
with a runtime subdir inside — `hub/agents/<id>/python/` for the Python wheel,
`hub/agents/<id>/npm/` for a JS/TS client, `hub/agents/<id>/cpp/` for a native
build. Each package depends on the published `amd-gaia` framework — third-party
contributors follow the exact same pattern AMD uses for its own agents.

## New here? Start with the examples

These reference agents are intentionally minimal, heavily commented, and built
to be **copy-pasted** as the starting point for your own agent. Read them in
order — each adds one concept:

| Example | Teaches | Tools |
|---------|---------|-------|
| [`hello-world/`](hello-world/python/) | The smallest possible agent: subclass `Agent`, set a system prompt. | 0 |
| [`word-count/`](word-count/python/) | Registering a tool with the `@tool` decorator. | 1 |
| [`doc-search/`](doc-search/python/) | Composing a framework mixin (`RAGToolsMixin`) for document Q&A. | 10 |

Each example's `README.md` explains the pattern; its `tests/` suite mocks the
LLM so it runs with no Lemonade server.

## Package layout

Every Python agent package has the same shape (see any example above):

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

That lands the package at `my-agent/python/`, matching every other agent here.
…or copy `hello-world/python/`, rename the package + class, and edit the prompt. See
[docs/guides/custom-agent.mdx](../../docs/guides/custom-agent.mdx) and the
[Agent Hub restructure spec](../../docs/spec/agent-hub-restructure.mdx).
