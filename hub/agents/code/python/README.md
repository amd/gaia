# gaia-agent-code

Standalone GAIA agent for autonomous code generation — planning, writing,
linting, fixing, and testing Python and Next.js/TypeScript projects. Depends on
the published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-code              # from PyPI (once published)
pip install -e hub/agents/code/python    # editable, for development
```

Installing registers the `code` agent via the `gaia.agent` entry-point group;
the GAIA registry discovers it automatically. It also installs the `gaia-code`
console script.

## Use

```bash
gaia-code "Create a Python CLI that fetches weather for a city"
```

`RoutingAgent` (in the core framework) also resolves this package through the
registry, so `gaia-code` style routing works once the wheel is installed.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/code/python/tests/ -x
```
