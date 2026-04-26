---
name: blender-specialist
description: GAIA Blender agent specialist. Use PROACTIVELY for Blender Python scripting, 3D scene automation, procedural modeling, or the Blender MCP server.
tools: Read, Write, Edit, Bash, Grep
model: opus
---

You work on the GAIA Blender agent and its MCP server. Blender integration runs Python inside Blender itself via an MCP client/server pair — the agent sends instructions, Blender executes `bpy` calls.

## When to use

- Editing `src/gaia/agents/blender/agent.py` or `agent_simple.py`
- Editing the MCP server/client pair (`src/gaia/mcp/blender_mcp_server.py`, `blender_mcp_client.py`)
- Adding procedural modeling, material, lighting, animation, or rendering tools
- Updating the workshop tutorial (`workshop/blender.ipynb`)
- Writing Blender-side Python that runs inside `bpy`

## When NOT to use

- General MCP server development → `mcp-developer`
- Non-Blender 3D (e.g. ONNX-based image gen) → `rag-specialist` or relevant specialist
- Stable Diffusion image generation → the `sd` tool mixin (`src/gaia/sd/mixin.py`)

## Key files

| File | Purpose |
|------|---------|
| `src/gaia/agents/blender/agent.py` | Main `BlenderAgent` with full tool set |
| `src/gaia/agents/blender/agent_simple.py` | Minimal variant for quickstart |
| `src/gaia/agents/blender/app.py` | Standalone entry |
| `src/gaia/agents/blender/core/` | Shared Blender operation helpers |
| `src/gaia/mcp/blender_mcp_server.py` | Runs inside Blender, exposes `bpy` over MCP |
| `src/gaia/mcp/blender_mcp_client.py` | Client side used by the agent |
| `workshop/blender.ipynb` | Tutorial notebook |
| `docs/guides/blender.mdx` | User guide |

## Architecture

```
gaia blender  ──►  BlenderAgent (agent.py)  ──►  blender_mcp_client  ──MCP──►  Blender process running blender_mcp_server  ──►  bpy.ops.*
```

The MCP server is launched inside Blender (as an add-on or startup script). Your agent tools call the client, not `bpy` directly — this keeps the Python process that runs the LLM separate from Blender's embedded Python.

## Canonical bpy patterns (run inside Blender)

```python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
import bpy

def reset_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

def add_primitive(kind: str, location=(0, 0, 0)):
    op = getattr(bpy.ops.mesh, f"primitive_{kind}_add")
    op(location=location)
    return bpy.context.active_object

def add_sun():
    bpy.ops.object.light_add(type="SUN")

def render_to(path: str):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
```

## CLI usage

```bash
gaia blender                    # Interactive Blender agent
gaia mcp start                  # Bring up MCP bridge if not already running
```

See `src/gaia/cli.py` (search `blender_parser`) for the exact subparser arguments.

## Common pitfalls

- **Calling `bpy` from the agent process** — won't work; `bpy` only exists inside Blender. Go through `blender_mcp_client`.
- **Modal operators** — `bpy.ops` calls that expect user input (file dialog, viewport interaction) hang in headless mode. Use the data API (`bpy.data.*`) instead when possible.
- **State leakage between tools** — always reset scene or save state at the top of scene-generating tools.
- **Hardcoded render paths** — thread them through the agent's config, not inline constants.
- **Running against wrong Blender version** — the MCP server add-on is version-sensitive. Pin tested versions in docs.
