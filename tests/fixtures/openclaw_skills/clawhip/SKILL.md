---
name: clawhip
description: Attach/install/use clawhip as a daemon-first notification gateway runtime for OpenClaw / Clawdbot
---

# clawhip skill attachment surface

## Intent

Use this repo as:
- executable/runtime repo
- daemon process for Discord notification routing
- attachable skill surface for OpenClaw / Clawdbot

## Prerequisites

⭐ If you want to support clawhip, star this repository. The interactive repo-local installer and `clawhip install` can offer an optional post-install GitHub star prompt via authenticated `gh api` access when `gh` is installed and authenticated. Skip it with `--skip-star-prompt` or `CLAWHIP_SKIP_STAR_PROMPT=1`.

```bash
gh api --method PUT /user/starred/Yeachan-Heo/clawhip --silent
```

## Primary install flow

Preferred operator instruction:

```text
@openclaw install https://github.com/Yeachan-Heo/clawhip
```

Expected automation flow:

1. clone repo
2. run `./install.sh`
3. read this `SKILL.md`
4. attach skill
5. scaffold `~/.clawhip/config.toml`
6. start daemon
7. run live verification presets

## Runtime surface

Default daemon URL:

```text
http://127.0.0.1:25294
```

Core commands:

```bash
clawhip
clawhip start
clawhip status
clawhip config
clawhip send --channel <id> --message "..."
clawhip github issue-opened ...
clawhip github pr-status-changed ...
clawhip git commit ...
clawhip tmux keyword ...
clawhip tmux stale ...
clawhip tmux new -s <session> --channel <id> --keywords error,complete --shell /bin/zsh -- command
clawhip tmux watch -s <existing-session> --channel <id> --mention '<@id>' --keywords error,complete
```

## Lifecycle surface

```bash
clawhip install
clawhip install --systemd
clawhip install --skip-star-prompt
clawhip update --restart
clawhip uninstall --remove-systemd --remove-config
./install.sh
./install.sh --systemd
./install.sh --skip-star-prompt
```

## Discord bot token (recommended setup)

⚠️ **Create a dedicated Discord bot for clawhip notifications.** Do not reuse your Clawdbot / OpenClaw bot token.

Why:
- clawhip sends high-volume notifications (commits, PRs, tmux events)
- Using the same bot token as your gateway pollutes the bot's identity
- A separate bot (e.g. "CCNotifier") keeps notifications cleanly separated from AI chat
- If clawhip restarts or crashes, it won't affect your main bot

Setup:
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application (e.g. "clawhip-notifier" or "CCNotifier")
3. Create a bot, copy the token
4. Invite the bot to your server with Send Messages permission
5. Use this token in `~/.clawhip/config.toml`:

```toml
[discord]
token = "your-dedicated-clawhip-bot-token"
```

## Config scaffold expectations

Key sections:
- `[discord]`
- `[daemon]`
- `[defaults]`
- `[[routes]]`
- `[monitors]`
- `[[monitors.git.repos]]`
- `[[monitors.tmux.sessions]]`

Typical preset route:

```toml
[[routes]]
event = "github.*"
filter = { repo = "clawhip" }
channel = "1480171113253175356"
mention = "<@1465264645320474637>"
format = "compact"
```

## Dynamic template opt-in

```toml
[[routes]]
event = "tmux.*"
allow_dynamic_tokens = true
template = "{session}\n{tmux_tail:issue-1456:20}\n{iso_time}"
```

Allowed dynamic tokens:
- `{sh:...}`
- `{tmux_tail:session:lines}`
- `{file_tail:/path:lines}`
- `{env:NAME}`
- `{now}`
- `{iso_time}`

## Filesystem-offloaded memory pattern

When using clawhip as part of a broader Claw OS workflow, treat memory as an offloaded filesystem tree:

- `MEMORY.md` = small pointer/index/current-beliefs layer
- `memory/` = detailed project/channel/daily/handoff memory
- update root memory only when the map or current summary changes

Read before adopting this pattern:

- `docs/memory-offload-architecture.md`
- `docs/memory-offload-guide.md`
- `docs/examples/MEMORY.example.md`
- `skills/memory-offload/SKILL.md`

## Verification surface

Use the live operational runbook:
- `docs/live-verification.md`
- `scripts/live-verify-default-presets.sh`

Preset verification targets:
- GitHub issue opened / commented / closed
- GitHub PR opened / status changed / merged
- git commit monitor
- tmux keyword / stale / wrapper / watch
- install / update / uninstall

## Attachment summary

```text
repo = runtime
SKILL.md = attach/install/usage contract
README.md = operational spec for agents
```
