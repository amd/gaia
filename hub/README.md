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

## Creating a new agent

```bash
gaia agent init my-agent --language python
```

See [docs/plans/agent-hub-ui.mdx](../docs/plans/agent-hub-ui.mdx) for the full Agent Hub platform plan.
