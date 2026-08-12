---
name: gui-agent
description: "GUI automation via visual perception. Screenshot → detect → click → verify. Use when asked to operate a desktop app, interact with a VM, or complete an OSWorld benchmark task."
---

# GUI Agent

Autonomous GUI task execution. Give it a natural language task, it operates the desktop.

## When to Use

Use `gui-agent` when the user asks you to:
- Operate a desktop application (click buttons, fill forms, navigate menus)
- Interact with a VM (OSWorld tasks)
- Do anything that requires seeing and clicking on a screen

## How to Use

```bash
gui-agent "your task description here"
```

Examples:

```bash
# Desktop automation
gui-agent "Open Firefox and go to google.com"
gui-agent "Send hello to John in WeChat"
gui-agent "Install the Orchis GNOME theme"

# Remote VM
gui-agent --vm http://172.16.82.132:5000 "Open GitHub in Chrome"

# With specific model
gui-agent --provider claude-code --model opus "Crop the top 20% of the image in GIMP"
```

## Options

```
gui-agent [OPTIONS] TASK

TASK                  Natural language task description

--vm URL              Remote VM HTTP API
--provider NAME       Force LLM provider: claude-code, openclaw, anthropic, openai
--model NAME          Override model name (e.g., opus, sonnet, gpt-4o)
--max-steps N         Max actions before stopping (default: 15)
--app NAME            App name for component memory (default: desktop)
```

## What It Does Internally

The agent runs an autonomous loop — you don't need to manage any of this:

1. **Observe** — screenshot + UI detection + component matching
2. **Verify** — check if the previous action succeeded
3. **Plan** — decide the next action (click, type, scroll, etc.)
4. **Execute** — perform the action
5. **Repeat** — until task is done or max steps reached

The agent learns UI components on first encounter and reuses them in future sessions.
