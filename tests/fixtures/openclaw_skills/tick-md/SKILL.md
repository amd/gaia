---
name: tick-md
description: Multi-agent task coordination via Git-backed Markdown. Claim tasks, prevent collisions, track history with automatic commits.
homepage: https://tick.md
metadata:
  openclaw:
    emoji: ✅
    kind: skill
---

# TICK.md

**Multi-agent task coordination via Git-backed Markdown**

Coordinate work across human and AI agents using structured TICK.md files. Built on Git, designed for natural language interaction, optimized for multi-agent workflows.

## ✨ Features

- 🤖 **AI-Native**: MCP server for seamless bot integration
- 📝 **Human-Readable**: Plain Markdown with YAML frontmatter
- 🔄 **Git-Backed**: Full version control and automatic audit trail
- 🎯 **Dependency Tracking**: Automatic task unblocking
- 🔒 **File Locking**: Prevents concurrent edit conflicts
- 🔍 **Advanced Filtering**: Find tasks by status, priority, tags
- 📊 **Visualization**: Dependency graphs (ASCII and Mermaid)
- 👀 **Real-Time Monitoring**: Watch mode for live updates
- 🌐 **Local-First**: No cloud required, works offline

## 🚀 Quick Start

### Install CLI
```bash
npm install -g tick-md
```

### Initialize Project
```bash
cd your-project
tick init
tick status
```

### Create and Claim Tasks
```bash
tick add "Build authentication" --priority high --tags backend
tick claim TASK-001 @yourname
tick done TASK-001 @yourname
```

## 🤖 For AI Agents

### Install MCP Server
```bash
npm install -g tick-mcp-server
```

Add to your MCP config (e.g., `~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "tick": {
      "command": "tick-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

### Using the MCP Tools

The MCP server exposes all CLI commands as tools:
- `tick_init` - Initialize a project
- `tick_add` - Create tasks
- `tick_claim` - Claim tasks for your agent
- `tick_done` - Mark tasks complete
- `tick_status` - Get project overview
- `tick_list` - Query and filter tasks
- `tick_comment` - Add progress notes

## 🎯 Key Concepts

### TICK.md File Structure

```markdown
---
meta:
  project_id: my-project
  id_prefix: TASK
  next_id: 4
  updated: 2026-02-07T15:30:00-05:00
---

### TASK-001 · Build user authentication

\`\`\`yaml
id: TASK-001
status: in_progress
priority: high
claimed_by: @claude-code
assigned_to: @claude-code
created_by: @gianni
created_at: 2026-02-07T10:00:00-05:00
updated_at: 2026-02-07T15:30:00-05:00
tags: [backend, auth]
depends_on: []
blocks: [TASK-002, TASK-003]
history:
  - ts: 2026-02-07T10:00:00  who: @gianni       action: created
  - ts: 2026-02-07T15:30:00  who: @claude-code  action: claimed
\`\`\`

> Build JWT-based authentication with refresh tokens
```

### Automatic Git Commits

By default, every mutation creates a git commit:
```bash
[tick] TASK-001 claimed by @claude-code
[tick] TASK-001 completed by @claude-code
[tick] TASK-003: created
```

Control with flags:
- `--commit` - Force commit
- `--no-commit` - Skip commit
- Or configure in `.tick/config.yml`

### Task States

- `backlog` - Not ready to start
- `todo` - Ready to claim
- `in_progress` - Being worked on
- `review` - Awaiting review
- `blocked` - Waiting on dependencies
- `done` - Completed

### File Locking

The CLI uses `.tick.lock` to prevent concurrent edits. When an agent claims a task, it acquires the lock. Other agents see the task is claimed and can't modify it.

## 📚 Documentation

- **Protocol Spec**: https://tick.md/docs/protocol
- **CLI Reference**: https://tick.md/docs/cli
- **Getting Started**: https://tick.md/docs/getting-started

## 💡 Example Workflow

```bash
# Human creates tasks
tick add "Build landing page" --priority high --tags frontend --assign @claude-code
tick add "Write launch email" --priority medium --tags content --assign @content-bot
tick add "E2E tests" --priority medium --tags qa --depends-on TASK-001

# AI agent picks up work
tick claim TASK-001 @claude-code
tick comment TASK-001 @claude-code --note "Built responsive landing with hero section"
tick done TASK-001 @claude-code

# Another agent auto-triggers
tick claim TASK-003 @qa-bot
tick done TASK-003 @qa-bot --skip-review
```

## 🔧 Configuration

Edit `.tick/config.yml`:
```yaml
git:
  auto_commit: true      # Auto-commit on mutations (default: true)
  commit_prefix: "[tick]"
  push_on_sync: false

locking:
  enabled: true
  timeout: 300

agents:
  default_trust: full
  require_registration: false
```

## 🌟 Best Practices for OpenClaw Bots

1. **Always claim before working**: `tick claim TASK-XXX @yourbotname`
2. **Add progress comments**: `tick comment TASK-XXX @yourbotname --note "Status update"`
3. **Mark done when complete**: `tick done TASK-XXX @yourbotname`
4. **Check dependencies**: Use `tick list --blocked` to find blocked tasks
5. **Use MCP server**: Enables natural language task management

## 🔧 OpenClaw Multi-Agent Setup

Step-by-step guide for coordinating multiple OpenClaw bots via TICK.md.

### 1. Create a shared tasks repo

```bash
gh repo create yourorg/project-tasks --private
cd project-tasks
npx tick-md init
git add -A && git commit -m "init" && git push
```

### 2. On each OpenClaw bot's server

```bash
cd /data/.openclaw/workspace
git clone git@github.com:yourorg/project-tasks.git tasks
cd tasks && npx tick-md status
```

### 3. Add to each bot's AGENTS.md

```markdown
## ✅ Task Coordination (TICK.md)

I coordinate with other agents via TICK.md in `./tasks/`.

### Before starting work:
1. `cd tasks && npx tick-md sync` — get latest
2. `npx tick-md list --status backlog --priority high` — find available work
3. `npx tick-md claim TASK-XXX @mybotname` — claim before working
4. `npx tick-md done TASK-XXX @mybotname` — mark complete

### Rules:
- **Never work on a task claimed by another agent**
- **Always claim before starting**
- **Sync before and after work**
```

### 4. Add to each bot's HEARTBEAT.md

```markdown
## Task Sync (TICK.md)

- [ ] Sync tasks: `cd tasks && npx tick-md sync`
- [ ] Check for my work: `npx tick-md list --claimed-by @mybotname --status in_progress`
- [ ] Check for available tasks: `npx tick-md list --status backlog --priority high`
```

### 5. Deploy keys

Each bot needs SSH access to the shared repo. Generate a deploy key on each server:

```bash
ssh-keygen -t ed25519 -C "botname@tasks" -f ~/.ssh/tasks_deploy -N ""
cat ~/.ssh/tasks_deploy.pub
# Add this to repo Settings → Deploy Keys (with write access)
```

Configure git to use it:

```bash
cd tasks
git config core.sshCommand "ssh -i ~/.ssh/tasks_deploy -o IdentitiesOnly=yes"
```

### 6. Sync command for bots

After any tick mutation, push changes:

```bash
cd tasks && git push
```

Or use `tick sync` which handles pull + push automatically.

## 📦 Package Info

- **CLI**: `tick-md` on npm
- **MCP Server**: `tick-mcp-server` on npm
- **GitHub**: https://github.com/Purple-Horizons/tick-md
- **License**: MIT

## 🆘 Support

- GitHub Issues: https://github.com/Purple-Horizons/tick-md/issues
- Sponsor: https://github.com/sponsors/Purple-Horizons
