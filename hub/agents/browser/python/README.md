# gaia-agent-browser

Standalone GAIA agent — web research (search, fetch, download). Depends on the published
`amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-browser              # from PyPI (once published)
pip install -e hub/agents/browser/python    # editable, for development
```

Installing registers the `web` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/browser/python/tests/ -x
```
