# Pipeline Orchestration: Root Cause Analysis

This document tracks all root causes found and fixed during the pipeline orchestration feature development.

## Summary

| RC# | Title | Status | Fixed In |
|-----|-------|--------|----------|
| RC1 | Single-turn agent passthrough — no tool execution loop | MITIGATED | Code block extraction approach |
| RC2 | Tool implementations missing — YAML declares tools with no Python backing | FIXED | `src/gaia/tools/` package |
| RC3 | System prompt files missing — agents get generic "You are X" prompt | FIXED | Fallback prompt with code instructions |
| RC4 | Thin user prompt — goal reaches LLM as 2-line string | PARTIALLY FIXED | Defect/iteration info now passed |
| RC5 | Save only writes JSON metadata — no code files extracted | FIXED | `artifact_extractor.py` |
| RC6 | System prompt path read from wrong attribute | FIXED | `configurable.py` |
| RC7 | Empty tool descriptions in system prompt | FIXED | Tools now registered via `gaia.tools` |
| RC8 | Defects and iteration count not passed to agents | FIXED | `_compose_user_prompt()` |

---

## RC1: Single-Turn Agent Passthrough

### What it is (simple explanation)
Think of it like hiring a contractor to build a house. The CORRECT way: you give them blueprints, they pick up tools, they build walls, install plumbing, wire electricity, and they show you the finished house.

WHAT ACTUALLY HAPPENS: you give them the blueprints, they write a summary of what they would build, hand you the summary, and leave. They never pick up a single tool.

The `ConfigurableAgent._run_agent_loop()` sends the goal to the LLM once, captures the raw text response, and returns it as the "artifact." It never parses the response for tool calls, never executes tools, and never loops back to ask follow-up questions.

### Where it occurs
`src/gaia/agents/configurable.py` -- `_run_agent_loop()` method (line ~325-385)

### The base Agent class HAS a full tool loop
`src/gaia/agents/base/agent.py` has `process_query()` (line ~1522) with a complete multi-turn agentic loop: send prompt, parse JSON, detect tool call, execute tool, feed result back, repeat. ConfigurableAgent bypasses all of this.

### Current mitigation
Instead of tool calling, we use **code block extraction**: the system prompt tells the LLM to produce fenced code blocks (` ```python filename=app.py `), and `artifact_extractor.py` parses and writes them to disk. This works with small models (0.6B) that cannot do structured JSON tool calling.

### Future fix
With a larger model (Qwen3.5-2B+), delegate to `process_query()` so agents can use actual tools (file_write, bash_execute, run_tests) in a multi-turn loop.

---

## RC2: Tool Implementations Missing

### What it is
The agent YAML files (e.g., `config/agents/senior-developer.yaml`) declare tools like `file_read`, `file_write`, `bash_execute`. But NO Python code existed to back these declarations. The `src/gaia/tools/` package did not exist.

When the pipeline tried to load tools, every import failed silently. Agents ran with zero registered tools -- they could not read files, write code, or run commands even if they wanted to.

### Where it occurs
- `config/agents/senior-developer.yaml` lines 40-46 (tool declarations)
- `src/gaia/agents/configurable.py` -- `_load_tool_module()` (import attempts)
- `src/gaia/tools/` (was missing entirely)

### Fix
Created `src/gaia/tools/` package with 7 standalone tool functions:
- `file_ops.py`: file_read, file_write, file_list
- `shell_ops.py`: bash_execute, run_tests
- `code_ops.py`: search_codebase, git_operations

All tools are decorated with `@tool` at module scope, use `workspace_dir` for sandboxing, and have path traversal protection.

### Status: FIXED

---

## RC3: System Prompt Files Missing

### What it is
Agent YAMLs reference system prompt files (e.g., `prompts/senior-developer.md`) but those files do not exist. The code falls back to a generic prompt: "You are Senior Developer. Your capabilities include: full-stack-development..." -- with zero instructions about HOW to produce code.

### Where it occurs
- `config/agents/senior-developer.yaml` line 38 (system_prompt field)
- `src/gaia/agents/configurable.py` -- `_get_system_prompt()` fallback

### Fix
Added code generation instructions to the fallback prompt: "When writing code, ALWAYS use fenced code blocks with the filename."

### Status: FIXED

---

## RC4: Thin User Prompt

### What it is (simple explanation)
When you tell the pipeline "Build a REST API", here is EVERYTHING the agent receives:

```
Goal: Build a REST API
Current phase: DEVELOPMENT
```

That is it. Two lines. No language specified. No framework. No endpoints. No data models. No directory structure. A 0.6B model receives this and produces a vague JSON summary because it has almost nothing to work with.

It is like emailing a developer "build something" with no requirements document, no wireframes, no tech stack decision -- just two words.

### Where it occurs
- `src/gaia/agents/configurable.py` -- `_compose_user_prompt()` method
- `src/gaia/pipeline/loop_manager.py` lines 515-524 (context dict construction)

### What was fixed
- User goal now injected into `exit_criteria` so agents get the actual goal text
- Defects and iteration count from previous iterations now included in the prompt
- On retry iterations, agents know what went wrong

### What remains
- Planning phase artifacts not yet passed as context to development agents
- No technology hints derived from goal text
- No workspace directory path in prompt
- No explicit output expectations

### Status: PARTIALLY FIXED

---

## RC5: Save Only Writes JSON Metadata

### What it is
The `--save` flag serialized `snapshot.to_dict()` as a single JSON file containing pipeline state, quality scores, and raw artifact text. Even when agents produced code, there were no actual `.py` files on disk -- just a JSON blob.

### Fix
Created `artifact_extractor.py` that parses fenced code blocks from LLM output and writes them to `{output_dir}/workspace/` as real files. The `--save` flag now creates both the JSON metadata AND actual code files.

### Status: FIXED

---

## RC6: System Prompt Path Read from Wrong Attribute

### What it is
`configurable.py` line 61 read the system prompt path from `definition.metadata.get("system_prompt")`, but the YAML stores it in `definition.system_prompt` (a top-level field). The metadata dict only contains author/tags. So the prompt file path was always None, even when correctly specified in YAML.

### Fix
Changed to check `definition.system_prompt` first, with fallback to `definition.metadata.get("system_prompt")`.

### Status: FIXED

---

## RC7: Empty Tool Descriptions in System Prompt

### What it is (simple explanation)
The system prompt is supposed to include a section listing available tools so the LLM knows what it CAN do. Like a menu at a restaurant -- if you do not show the customer the menu, they cannot order.

Because tools were not registered (RC#2), the `_format_tools_for_prompt()` method iterated over an empty registry. The LLM received a system prompt with no tools section. It had no idea it could write files, run commands, or search code.

### Where it occurs
- `src/gaia/agents/configurable.py` -- `rebuild_system_prompt()` and `_format_tools_for_prompt()`
- `src/gaia/agents/base/tools.py` -- `_TOOL_REGISTRY` was empty for pipeline agents

### Fix
With RC#2 fixed (tools package created), tools are now registered in `_TOOL_REGISTRY` when imported. The system prompt's tool description section now lists available tools with their parameters and descriptions. Additionally, `_load_tool_module()` now consults `TOOL_MODULE_MAP` as its first resolution strategy, ensuring that YAML tool names like `file_read` resolve to `gaia.tools.file_ops` without relying on a module named `gaia.tools.file_read` to exist.

### Status: FIXED (dependent on RC#2)

---

## RC8: Defects and Iteration Count Not Passed to Agents

### What it is
The loop manager passes `iteration` and `defects` in the context dict, but `_compose_user_prompt()` only used `goal`, `phase`, and `artifacts`. On retry iterations (when quality did not pass), the agent had no idea what went wrong in the previous attempt.

### Fix
Added iteration awareness and defect propagation to `_compose_user_prompt()`.

### Status: FIXED
