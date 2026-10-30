# gaia-agent-chat

The conversational ChatAgent — the class `GaiaAgent` subclasses, so this is a
hard dependency of `gaia-agent` and fully supported as a library. It is no
longer a product of its own: the flagship supersedes its three prompt profiles
(`chat`, `doc`, `file`), and all three are retired as user-facing choices.
Depends on the published `amd-gaia` framework wheel.

## Install

`gaia-agent-chat` is not on PyPI yet ([#2240](https://github.com/amd/gaia/issues/2240)),
and `gaia init` / `gaia hub install gaia` don't install it — they install the
flagship as a native binary. When GAIA needs this package it prints the exact
install command (the Agent UI, `gaia chat`): run that command, then restart the
Agent UI server. It targets the Python GAIA runs in and the release tag that
matches your `amd-gaia`.

To install it yourself, do the same two things — install into the environment
GAIA runs in, and pin the tag of your `amd-gaia` version (`gaia --version`;
`0.24.1` is tagged `v0.24.1`, and a release candidate `0.25.0rc12` is tagged
`v0.25.0-rc12`). `--python` names that environment's interpreter; drop it if the
environment is already activated:

```bash
uv pip install --python <path-to-GAIA's-python> "gaia-agent-chat @ git+https://github.com/amd/gaia.git@v0.24.1#subdirectory=hub/agents/chat/python"
pip install -e hub/agents/chat/python    # editable, from a checkout, for development
```

Installing into a different environment than the one serving the Agent UI is
why it keeps asking for the package after an install; an unpinned URL installs
`main`, which can fail to import against a released `amd-gaia`.

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

## Provider migration

The direct OpenAI and LiteLLM adapters are removed. `--use-chatgpt` and
`use_chatgpt=True` remain only to report an actionable migration error before
startup. Configure your model in Lemonade, then select its catalog ID and the
Lemonade server URL. The local Lemonade and Claude routes are unchanged.
See [gateway migration](https://amd-gaia.ai/docs/sdk/sdks/llm#gateway-migration).
