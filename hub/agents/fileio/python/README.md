# gaia-agent-fileio

Standalone GAIA building-block agent for reading, writing, and editing files on
the local filesystem. Depends on the published `amd-gaia` framework wheel.

This is an internal building-block agent (hidden from the UI selector by
default); other agents and flows compose it through the registry.

## Install

```bash
pip install gaia-agent-fileio               # from PyPI (once published)
pip install -e hub/agents/fileio/python     # editable, for development
```

Installing registers the `fileio` agent via the `gaia.agent` entry-point group.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/fileio/python/tests/ -x
```
