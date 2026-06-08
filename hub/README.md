# GAIA Agent Hub

Production agents for the GAIA Agent Hub. Each agent is a standalone package that depends on the published `amd-gaia` PyPI package.

## Structure

```
hub/
├── agents/
│   ├── python/         # Python agents (packaged as wheels)
│   └── cpp/            # C++ agents (compiled binaries)
└── README.md
```

## New here? Start with the examples

Minimal, heavily-commented reference agents live under
[`agents/python/`](agents/python/) and are built to be copy-pasted as the
starting point for your own agent:

- [`hello-world/`](agents/python/hello-world/) — the smallest possible agent (no tools)
- [`word-count/`](agents/python/word-count/) — registering a tool with `@tool`
- [`doc-search/`](agents/python/doc-search/) — composing the framework `RAGToolsMixin`

See [agents/python/README.md](agents/python/README.md) for the package format and a
guided reading order.

## Creating a new agent

```bash
gaia agent init my-agent --language python
```

…or copy one of the examples above. See
[docs/plans/agent-hub-ui.mdx](../docs/plans/agent-hub-ui.mdx) for the full Agent
Hub platform plan and
[docs/spec/agent-hub-restructure.mdx](../docs/spec/agent-hub-restructure.mdx)
for the package format.
