---
name: "gaia-build-agent"
description: "Build a new GAIA agent with the SDK end-to-end: scaffold the package, write the Agent subclass, register @tool functions, compose reusable tool mixins, set the model + system prompt, and test it locally — then hand off to publishing. Use when creating a NEW agent (a Python class inheriting from the base Agent) for the GAIA repo or a user-authored package, not for tuning an existing agent's prompt (prompt-engineer), adding one tool to an existing class (python-developer), or shipping/releasing an already-built agent (use the agent-hub-release skill + docs/guides/hub-publishing.mdx). Pairs with the agent-hub-release skill: this one BUILDS, that one PUBLISHES."
---

# Building a GAIA Agent

How to take an idea to a working, testable GAIA agent — a Python class that runs
locally on AI PCs. This skill covers **build**; publishing is its sibling
[`agent-hub-release`](../agent-hub-release/SKILL.md) skill and the author guide
[`docs/guides/hub-publishing.mdx`](../../../docs/guides/hub-publishing.mdx). The
full prose walkthrough is [`docs/guides/custom-agent.mdx`](../../../docs/guides/custom-agent.mdx).

> Read [`CLAUDE.md`](../../../CLAUDE.md) first — the "No Silent Fallbacks", code-reuse,
> testing, and eval rules all apply to a new agent.

## The mental model

A GAIA agent is **one Python class** that inherits from the base `Agent`
(`src/gaia/agents/base/agent.py`) and exposes capabilities as `@tool`-decorated
methods. The base class owns the agent loop, tool registry, state, error recovery,
and (via mixins) MCP / OpenAI-API exposure — you write the prompt + the tools, not
the plumbing. There are no YAML agent manifests for in-core agents (removed in
v0.17.5); a *publishable hub package* adds a `gaia-agent.yaml` manifest on top (see
the publish skill).

## Steps

1. **Scaffold.** `gaia agent init my-agent` generates a starter package at
   `./my-agent/`. For a publishable hub package in this repo, add `--layout hub`
   so it lands at `hub/agents/<id>/python/`:

   ```bash
   gaia agent init my-agent -o hub/agents/ --layout hub
   ```

   Then mirror an existing package (e.g. `analyst`, `browser`) for structure.

2. **Write the `Agent` subclass.** Inherit from `Agent` (or a closer base like the
   chat/docqa agents). Set the model with `model_id` — omit it only if the
   `Qwen3.5-35B-A3B-GGUF` base default is right; chat-style agents use
   `Gemma-4-E4B-it-GGUF` (`DEFAULT_MODEL_NAME`). Keep the constructor thin.

3. **Register tools with `@tool`.** Each `@tool` method is a capability the LLM can
   call; its **docstring is the schema the model sees**, so write it for the model
   (one line of intent + each arg). Return structured data or an actionable error —
   never swallow exceptions into a placeholder (No Silent Fallbacks).

4. **Reuse, don't reinvent — compose mixins.** Before writing file/web/RAG/shell/SQL
   logic, check `KNOWN_TOOLS` in [`src/gaia/agents/registry.py`](../../../src/gaia/agents/registry.py):
   `rag`, `file_io`, `file_search`, `shell`, `browser`, `scratchpad`, `code_index`,
   `vlm`, `sd`, … Compose them by name instead of duplicating. New shared logic →
   add a mixin and register it in `KNOWN_TOOLS`.

5. **System prompt.** Implement `_get_system_prompt()` — state the agent's job, when
   to use which tool, and the output contract. This is an LLM-affecting surface, so
   it's covered by the eval rule below.

6. **Make it discoverable.** An **in-core** agent is added to
   [`src/gaia/agents/registry.py`](../../../src/gaia/agents/registry.py) (and a
   `gaia <cmd>` subparser in `src/gaia/cli.py` if it's a CLI command). A **hub
   package** is **auto-discovered from its `gaia-agent.yaml` manifest** — no
   `registry.py` edit; just make sure the manifest's `python.entry_module` /
   `entry_class` point at your class.

7. **Test it — actually run it.** Unit tests with a mocked LLM for tool logic
   (`tests/`), then run the **real CLI** a user would (`gaia <cmd> ...` or
   `python my_agent.py`) — never only the Python module. Follow the
   [`gaia-testing`](../gaia-testing/SKILL.md) skill for real-world evidence.

8. **Eval if it touches LLM behavior.** If you wrote/changed a system prompt, tool
   docstrings, the tool schema, the model, or error classification, you MUST run
   `gaia eval agent` against the relevant category and compare to the committed
   baseline before calling it done (CLAUDE.md eval rule). Unit tests don't catch LLM
   regressions.

## Then publish

Once it runs and is documented (README/SPEC/SKILL per the doc-sync rule), ship it:

- **Standard wheel → Hub + PyPI** (PR route, no token): the
  [`hub-publishing.mdx`](../../../docs/guides/hub-publishing.mdx) guide.
- **Frozen-binary + npm sidecar** (like the email agent): the
  [`agent-hub-release`](../agent-hub-release/SKILL.md) skill.

## Reference

- [`docs/guides/custom-agent.mdx`](../../../docs/guides/custom-agent.mdx) — the full build walkthrough.
- [`src/gaia/agents/base/`](../../../src/gaia/agents/base/) — `Agent`, `MCPAgent`, `ApiAgent`, `@tool`, console, errors.
- [`src/gaia/agents/registry.py`](../../../src/gaia/agents/registry.py) — agent registry + `KNOWN_TOOLS`.
- The `gaia-agent-builder` agent (`.claude/agents/`) — a specialist for this work.
