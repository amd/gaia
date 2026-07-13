# gaia-agent-analyst

Standalone GAIA agent — structured data analysis (CSV/Excel, scratchpad SQL). Depends on the published
`amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-analyst              # from PyPI (once published)
pip install -e hub/agents/analyst/python    # editable, for development
```

Installing registers the `data` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/analyst/python/tests/ -x
```
