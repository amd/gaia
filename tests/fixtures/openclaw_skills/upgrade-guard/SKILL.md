---
name: upgrade-guard
description: Safe OpenClaw upgrades with snapshot, pre-flight checks, controlled upgrade steps, post-verification, and emergency rollback. Never lose your working system to a bad update again.
metadata:
  openclaw:
    emoji: "🔄"
---

# Upgrade Guard 🔄

**Never lose a working OpenClaw to a bad upgrade again.**

Born from 7 cascading failures during a single version jump. The gateway crashed, Telegram disconnected, plugins broke, models vanished — and the AI agent that caused it was also dead, so nobody could fix it remotely.

This skill makes upgrades safe.

## The Problem

OpenClaw upgrades can break in ways that are invisible until it's too late:

| Failure type | Example |
|---|---|
| Plugin renames | `clawdbot.plugin.json` → `openclaw.plugin.json` |
| Dependency breaks | SDK module paths change, exports shift |
| Config schema changes | New required fields, removed fields |
| Model name changes | Dot vs hyphen format changes |
| Channel config wipes | Silent removal during migration |

A single `git pull && pnpm install` can trigger all of these simultaneously.

## What Upgrade Guard Does

### 1. Snapshot (`snapshot`)
Captures your entire working state:
- Version + git commit
- Full config backup
- Plugin file inventory
- Symlink map
- Lock file
- Channel list + model info
- Gateway health status

### 2. Pre-flight (`check`)
Before touching anything:
- Snapshot exists?
- Config valid?
- Git repo clean?
- Disk space OK?
- Package manager available?
- Remote changes preview
- Breaking change detection (scans commit messages)

### 3. Safe Upgrade (`upgrade`)
Controlled 6-step process:
1. Pre-flight checks (abort if fail)
2. Fresh snapshot
3. Stop gateway
4. `git pull` (rollback on fail)
5. `pnpm install` + `pnpm run build` (rollback on fail)
6. Post-upgrade verification

### 4. Verification (`verify`)
After upgrade, checks everything:
- Version actually changed?
- Plugin files renamed/removed? (detects clawdbot↔openclaw renames)
- Config still valid JSON?
- All channels still configured?
- Model still set?
- No broken symlinks?
- Gateway starts and responds?
- No errors in recent logs?

### 5. Rollback (`rollback`)
Emergency restore:
- Stop gateway
- Restore git to previous commit
- Reinstall old dependencies
- Restore config
- Restart gateway

## Usage

```bash
# Before upgrading: take a snapshot
bash scripts/upgrade-guard.sh snapshot

# Check what's coming
bash scripts/upgrade-guard.sh check

# See current state
bash scripts/upgrade-guard.sh status

# Do the upgrade (safe, with auto-rollback on failure)
bash scripts/upgrade-guard.sh upgrade

# Or just preview without changing anything
bash scripts/upgrade-guard.sh upgrade --dry-run

# After upgrade: verify everything works
bash scripts/upgrade-guard.sh verify

# Something broke? Emergency rollback
bash scripts/upgrade-guard.sh rollback
```

## For AI Agents

**MANDATORY before any OpenClaw upgrade:**

1. `upgrade-guard.sh snapshot` — save current state
2. `upgrade-guard.sh check` — verify pre-conditions
3. `upgrade-guard.sh upgrade` — let the script handle it
4. If anything fails → `upgrade-guard.sh rollback`

**NEVER run blind `git pull && pnpm install` without snapshotting first.**

## What It Catches That config-guard Doesn't

| Check | config-guard | upgrade-guard |
|---|---|---|
| Bad config values | ✅ | ❌ |
| Plugin file renames | ❌ | ✅ |
| Dependency breaks | ❌ | ✅ |
| Broken symlinks | ❌ | ✅ |
| Version tracking | ❌ | ✅ |
| Git state management | ❌ | ✅ |
| Gateway log analysis | ❌ | ✅ |
| Full system rollback | ❌ | ✅ |

Use both together: config-guard for config changes, upgrade-guard for version upgrades.

## Watchdog — OS-Level Self-Healing

The watchdog runs independently via systemd timer. It survives gateway crashes, AI agent death, and server reboots.

```bash
# Install (checks every 60 seconds)
bash scripts/watchdog.sh install

# Check health manually
bash scripts/watchdog.sh check

# View status and recent events
bash scripts/watchdog.sh status

# Remove
bash scripts/watchdog.sh uninstall
```

### Recovery strategy:
| Consecutive failures | Action |
|---|---|
| 1-2 | Log and wait |
| 3 | Restart gateway |
| 6+ | Rollback to last snapshot |

### What it checks:
1. **Process** — is the gateway process running?
2. **HTTP** — does the gateway respond on its port?
3. **Telegram** — any connection errors in recent logs?

### Why it works when everything else fails:
- Runs as **systemd timer** — survives gateway crash, AI death, reboots
- Checks every **60 seconds** — detects problems fast
- **5-minute cooldown** between actions — no restart loops
- Uses upgrade-guard's **rollback** — full version restore if restart doesn't help
- **Logs everything** to `watchdog.log` for post-mortem

## Install

```bash
clawdhub install upgrade-guard
# or clone directly
git clone https://github.com/jzOcb/upgrade-guard
```

## Requirements

- `bash` 4+
- `python3`
- `curl`
- `git`
- `pnpm` or `npm`

## Related

- [config-guard](https://github.com/jzOcb/config-guard) — Config validation and auto-rollback
- [agent-guardrails](https://github.com/jzOcb/agent-guardrails) — Code-level enforcement for AI agents
