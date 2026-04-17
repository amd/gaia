# GAIA Development Container

A fully configured dev container for GAIA with Python 3.12, Claude Code, and AMD-optimized tooling.

## What's included

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12 | Primary runtime |
| Node.js | 20 | Claude Code and tooling |
| git-delta | 0.18.2 | Enhanced git diffs |
| zsh + Powerlevel10k | latest | Shell experience |
| Claude Code | latest | AI-assisted development |
| GitHub CLI (`gh`) | latest | PR and issue management |

## Getting started

1. Open the repository in VS Code
2. When prompted, click **Reopen in Container**
3. Wait for `postCreateCommand` to finish (pip install + initial setup)
4. Set your `ANTHROPIC_API_KEY` in your local environment before opening the container

## Port forwarding

| Port | Service | Notification |
|------|---------|-------------|
| 8000 | Lemonade Server | Notify |
| 8080 | GAIA API / MCP Bridge | Notify |
| 3000 | Eval Visualizer | Silent |

## Running GAIA

```bash
# Start the LLM backend
lemonade-server serve

# Test LLM connectivity
gaia llm "Hello"

# Start interactive chat
gaia chat

# Start API server (port 8080)
gaia api start

# Start MCP bridge (port 8080)
gaia mcp start --background
gaia mcp status
```

## Claude Code setup

`ANTHROPIC_API_KEY` is passed through from your local environment automatically. Set it before opening the container:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Model defaults are pre-configured:
- `ANTHROPIC_MODEL=opusplan` — used by Claude Code for planning
- `ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4-5-20251001` — used for quick tasks

Claude Code is installed on first container start via `postStartCommand`. Run `claude --version` to confirm.

## Plugins

The `.claude/settings.json` enables these plugins for all team members:

- `github` — GitHub Issues and PR integration
- `context7` — Up-to-date library documentation
- `commit-commands` — `/commit`, `/push`, `/pr` slash commands
- `code-simplifier` — Code quality reviews

Copy `.claude/settings.json.example` to `.claude/settings.local.json` to add personal settings (MCP servers, permissions, status line).

## Persistent volumes

| Volume | Mount | Purpose |
|--------|-------|---------|
| `gaia-bashhistory-*` | `/commandhistory` | Shell history across rebuilds |
| `gaia-config-*` | `/home/gaia/.claude` | Claude Code config and credentials |

Volumes survive container rebuilds. To reset them, delete the named Docker volumes.

## MCP configuration

`.devcontainer/.mcp.json` is an empty template copied to `/workspace/.mcp.json` on create. Add MCP servers there:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["-y", "@my-org/mcp-server"]
    }
  }
}
```

For MCP servers running on the host machine, use `host.docker.internal` instead of `localhost`:

```json
{
  "mcpServers": {
    "host-server": {
      "type": "http",
      "url": "http://host.docker.internal:9000"
    }
  }
}
```

## Troubleshooting

**Permission errors on `/home/gaia/.claude`:**
The `postStartCommand` runs `sudo chown -R gaia:gaia /home/gaia/.cache /home/gaia/.claude` on each start to fix volume ownership. If you see permission errors, rebuild the container.

**Port already in use:**
Another container may be using the same port. Stop it with `docker ps` and `docker stop <name>`, then restart the dev container.

**Lemonade Server not starting:**
Lemonade requires AMD hardware (Ryzen AI NPU/GPU) for acceleration. Inside the container, it runs in CPU fallback mode. For full NPU support, run `lemonade-server serve` on the host and point `LEMONADE_BASE_URL` to `http://host.docker.internal:8000`.

**Claude CLI not found after rebuild:**
Claude Code is installed by `postStartCommand` which runs on each container start. If `claude` is not on PATH, run:
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
