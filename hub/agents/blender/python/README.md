# gaia-agent-blender

Standalone GAIA agent for 3D scene automation in Blender via MCP. Depends on the
published `amd-gaia` framework wheel and a running Blender MCP server.

## Install

```bash
pip install gaia-agent-blender            # from PyPI (once published)
pip install -e hub/agents/blender/python  # editable, for development
```

Installing registers the `blender` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Use

```bash
gaia blender
```

Or programmatically:

```python
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
agent = registry.create_agent("blender")
```

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/blender/python/tests/ -x
```
