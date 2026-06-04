# gaia-agent-emr

Standalone GAIA agent for medical form intake and extraction. Uses a vision
LLM (VLM) to extract structured data from scanned intake forms into a local
patient database. Depends on the published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-emr               # from PyPI (once published)
pip install -e hub/agents/python/emr     # editable, for development
```

Installing registers the `emr` agent via the `gaia.agent` entry-point group;
the GAIA registry discovers it automatically.

## Use

```bash
gaia emr        # via the GAIA CLI
gaia-emr        # standalone console script
```

Or programmatically:

```python
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
agent = registry.create_agent("emr")
```

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/python/emr/tests/ -x
```
