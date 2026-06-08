# gaia-agent-docker

Standalone GAIA agent for Docker container management. Depends on the published
`amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-docker                # from PyPI (once published)
pip install -e hub/agents/python/docker      # editable, for development
```

Installing registers the `docker` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Use

```bash
gaia docker "list my running containers"
```

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/python/docker/tests/ -x
```
