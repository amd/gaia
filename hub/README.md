# Packages GAIA ships

The packages GAIA builds and ships beside the `amd-gaia` core: the GAIA agent's Python
harness, the front ends' release manifests, and the skills. There is no user-facing
agent catalog; `hub.amd-gaia.ai` is the download server `gaia init`, the daemon and the
installers fetch from.

## Structure

Three lanes, each with its own package format and publish contract:

```
hub/
├── agents/            # Agents — a package that runs on its own
│   ├── <id>/
│   │   ├── python/     # Python agent (packaged as a wheel)
│   │   ├── npm/        # JS/TS client (when the agent ships one)
│   │   └── cpp/        # C++ agent (compiled binary, when present)
│   └── README.md
├── components/        # Front ends that drive agents (Agent UI, Terminal UI)
│   └── <id>/gaia-agent.yaml
├── skills/            # Skills — a capability any agent composes
│   ├── <name>/SKILL.md
│   └── README.md
└── README.md
```

Agents and components declare a `gaia-agent.yaml`; skills do not — a skill's
`SKILL.md` front matter *is* its manifest. See
[skills/README.md](skills/README.md).

## New here?

`gaia agent init` scaffolds a working agent package. The smallest complete
reference package is
[`tests/fixtures/agents/hello-world/`](../tests/fixtures/agents/hello-world/);
see [agents/README.md](agents/README.md) for the package format.

## Creating a new agent

```bash
gaia agent init my-agent --language python -o agents/ --layout hub
```

That lands the package at `agents/my-agent/python/`, matching the layout of every
agent in this tree. See
[docs/spec/agent-hub-restructure.mdx](../docs/spec/agent-hub-restructure.mdx)
for the package format.
