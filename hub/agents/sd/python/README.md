# gaia-agent-sd

Standalone GAIA agent for Stable Diffusion image generation with LLM-enhanced
prompts. Depends on the published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-sd               # from PyPI (once published)
pip install -e hub/agents/sd/python     # editable, for development
```

Installing registers the `sd` agent via the `gaia.agent` entry-point group;
the GAIA registry discovers it automatically.

## Use

```bash
gaia sd "a watercolor painting of a fox in autumn"
```

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/sd/python/tests/ -x
```
