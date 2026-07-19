# GAIA Agent Hub

Production agents for the GAIA Agent Hub. Each agent is a standalone package that depends on the published `amd-gaia` PyPI package.

## Structure

```
hub/
├── agents/
│   ├── <id>/
│   │   ├── python/     # Python agent (packaged as a wheel)
│   │   ├── npm/        # JS/TS client (when the agent ships one)
│   │   └── cpp/        # C++ agent (compiled binary, when present)
│   └── README.md
└── README.md
```

## New here? Start with the examples

Minimal, heavily-commented reference agents live under
[`agents/`](agents/) and are built to be copy-pasted as the
starting point for your own agent:

- [`hello-world/`](agents/hello-world/python/) — the smallest possible agent (no tools)
- [`word-count/`](agents/word-count/python/) — registering a tool with `@tool`
- [`doc-search/`](agents/doc-search/python/) — composing the framework `RAGToolsMixin`

See [agents/README.md](agents/README.md) for the package format and a
guided reading order.

## Creating a new agent

```bash
gaia agent init my-agent --language python -o agents/ --layout hub
```

That lands the package at `agents/my-agent/python/`, matching the layout of every
agent in this tree. …or copy one of the examples above. See
[docs/plans/agent-hub-ui.mdx](../docs/plans/agent-hub-ui.mdx) for the full Agent
Hub platform plan and
[docs/spec/agent-hub-restructure.mdx](../docs/spec/agent-hub-restructure.mdx)
for the package format.
