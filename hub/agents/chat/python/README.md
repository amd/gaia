# gaia-agent-chat

The conversational ChatAgent — the class `GaiaAgent` subclasses, so this is a
hard dependency of `gaia-agent` and fully supported as a library. It is no
longer a product of its own: the flagship supersedes its three prompt profiles
(`chat`, `doc`, `file`), and all three are retired as user-facing choices.
Depends on the published `amd-gaia` framework wheel.

## Install

```bash
pip install gaia-agent-chat              # from PyPI (once published — see #2240)
pip install -e hub/agents/chat/python    # editable, for development
uv pip install "gaia-agent-chat @ git+https://github.com/amd/gaia.git#subdirectory=hub/agents/chat/python"  # works today without a repo checkout
```

Installing registers `chat`, `doc`, and `file` via the `gaia.agent` entry-point
group, all marked `hidden`: the registry resolves them by id — which is what
keeps stored sessions, the `*-lite` aliases, and the eval scenarios working —
but they are absent from the Agent UI picker and the Hub catalog. Pick the
flagship `gaia` agent instead.

`gaia chat` and `gaia chat --ui` import `ChatAgent` directly rather than going
through the registry, so both are unaffected by the hidden flag.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/chat/python/tests/ -x
```
