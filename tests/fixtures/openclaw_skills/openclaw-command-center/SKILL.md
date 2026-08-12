---
name: command-center
version: 1.4.1
description: Mission control dashboard for OpenClaw - real-time session monitoring, LLM usage tracking, cost intelligence, and system vitals. View all your AI agents in one place.
metadata:
  openclaw:
    requires:
      node: ">=18"
    install:
      - id: start
        kind: shell
        command: "node lib/server.js"
        label: "Start Command Center (http://localhost:3333)"
---

# OpenClaw Command Center

Mission control for your AI workforce.

## Quick Start

```bash
npx clawhub@latest install command-center
cd skills/command-center
node lib/server.js
```

Dashboard runs at **http://localhost:3333**

## Features

- **Session Monitoring** — Real-time view of all AI sessions with live updates
- **LLM Fuel Gauges** — Track Claude, Codex, and other model usage
- **System Vitals** — CPU, Memory, Disk, Temperature
- **Cron Jobs** — View and manage scheduled tasks
- **Cerebro Topics** — Automatic conversation organization
- **Cost Tracking** — Per-session costs, projections, savings estimates
- **Privacy Controls** — Hide sensitive topics for demos

## Configuration

The dashboard auto-detects your OpenClaw workspace. Set `OPENCLAW_WORKSPACE` to override.

### Authentication

| Mode         | Use Case          |
| ------------ | ----------------- |
| `none`       | Local development |
| `token`      | Remote access     |
| `tailscale`  | Team VPN          |
| `cloudflare` | Public deployment |

```bash
DASHBOARD_AUTH_MODE=tailscale node lib/server.js
```

## API

| Endpoint          | Description                  |
| ----------------- | ---------------------------- |
| `GET /api/state`  | All dashboard data (unified) |
| `GET /api/events` | SSE stream for live updates  |
| `GET /api/health` | Health check                 |

## Links

- [GitHub](https://github.com/jontsai/openclaw-command-center)
- [ClawHub](https://www.clawhub.ai/jontsai/command-center)
- [Documentation](https://github.com/jontsai/openclaw-command-center#readme)
