---
name: total-recall
description: "The only memory skill that watches on its own. No database. No vectors. No manual saves. Just an LLM observer that compresses your conversations into prioritised notes, consolidates when they grow, and recovers anything missed. Five layers of redundancy, zero maintenance. ~$0.00/month (using free-tier models). While other memory skills ask you to remember to remember, this one just pays attention."
metadata:
  openclaw:
    emoji: "🧠"
    requires:
      bins: ["jq", "curl"]
    env:
      - key: OPENROUTER_API_KEY
        label: "OpenRouter API key (for LLM calls)"
        required: true
    config:
      memorySearch:
        description: "Enable memory search on observations.md for cross-session recall"
---

# Total Recall — Autonomous Agent Memory

**The only memory skill that watches on its own.**

No database. No vectors. No manual saves. Just an LLM observer that compresses your conversations into prioritised notes, consolidates when they grow, and recovers anything missed. Five layers of redundancy, zero maintenance. ~$0.00/month (using free-tier models).

While other memory skills ask you to remember to remember, this one just pays attention.

## Architecture

```
Layer 1: Observer (cron, every 15-30 min)
    ↓ compresses recent messages → observations.md
Layer 2: Reflector (auto-triggered when observations > 8000 words)
    ↓ consolidates, removes superseded info → 40-60% reduction
Layer 3: Session Recovery (runs on every /new or /reset)
    ↓ catches any session the Observer missed
Layer 4: Reactive Watcher (inotify daemon, Linux only)
    ↓ triggers Observer after 40+ new JSONL writes, 5-min cooldown
Layer 5: Pre-compaction hook (memoryFlush)
    ↓ emergency capture before OpenClaw compacts context
```

## What It Does

- **Observer** reads recent session transcripts (JSONL), sends them to an LLM, and appends compressed observations to `observations.md` with priority levels (high, medium, low)
- **Reflector** kicks in when observations grow too large, consolidating related items and dropping stale low-priority entries
- **Session Recovery** runs at session start, checks if the previous session was captured, and does an emergency observation if not
- **Reactive Watcher** watches the session directory with inotify so high-activity periods get captured faster than the cron interval
- **Pre-compaction hook** fires when OpenClaw is about to compact context, ensuring nothing is lost

## Quick Start

### 1. Install the skill
```bash
clawdhub install total-recall
```

### 2. Set your API key
Add to your `.env` or OpenClaw config:
```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxx
```

### 3. Run the setup script
```bash
bash skills/total-recall/scripts/setup.sh
```

This will:
- Create the memory directory structure (`memory/`, `logs/`, backups)
- On Linux with inotify + systemd: install the reactive watcher service
- Print cron job and agent configuration instructions for you to add manually

### 4. Configure your agent to load observations

Add to your agent's workspace context (e.g., `MEMORY.md` or system prompt):
```
At session startup, read `memory/observations.md` for cross-session context.
```

Or use OpenClaw's `memoryFlush.systemPrompt` to inject a startup instruction.

## Platform Support

| Platform | Observer + Reflector + Recovery | Reactive Watcher |
|----------|-------------------------------|-----------------|
| Linux (Debian/Ubuntu/etc.) | Full support | With inotify-tools |
| macOS | Full support | Not available (cron-only) |

All core scripts use portable bash. `stat`, `date`, and `md5` commands are handled cross-platform via `_compat.sh`.

## Configuration

All scripts read from environment variables with sensible defaults:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | (required) | OpenRouter API key for LLM calls |
| `MEMORY_DIR` | `$OPENCLAW_WORKSPACE/memory` | Where observations.md lives |
| `SESSIONS_DIR` | `~/.openclaw/agents/main/sessions` | OpenClaw session transcripts |
| `OBSERVER_MODEL` | `stepfun/step-3.5-flash:free` | Primary model for compression (free) |
| `OBSERVER_FALLBACK_MODEL` | `nvidia/nemotron-3-nano-30b-a3b:free` | Fallback if primary fails (free) |
| `OBSERVER_LOOKBACK_MIN` | `15` | Minutes to look back (daytime) |
| `OBSERVER_MORNING_LOOKBACK_MIN` | `480` | Minutes to look back (before 8am) |
| `OBSERVER_LINE_THRESHOLD` | `40` | Lines before reactive trigger (Linux) |
| `OBSERVER_COOLDOWN_SECS` | `300` | Cooldown between reactive triggers (Linux) |
| `REFLECTOR_WORD_THRESHOLD` | `8000` | Words before reflector runs |
| `REFLECTOR_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` | Model for consolidating observations (free) |
| `REFLECTOR_FALLBACK_MODEL` | `openrouter/hunter-alpha` | Fallback if reflector model fails (free) |
| `OPENCLAW_WORKSPACE` | `~/your-workspace` | Workspace root |

## LLM Provider Configuration

Total Recall uses any OpenAI-compatible chat completion API. Switch providers by setting environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | API endpoint |
| `LLM_API_KEY` | falls back to `OPENROUTER_API_KEY` | API key |
| `LLM_MODEL` | `deepseek/deepseek-v3.2` | Model to use |

### Provider examples

```bash
# OpenRouter (default)
export OPENROUTER_API_KEY="your-key"

# Ollama (local)
export LLM_BASE_URL="http://localhost:11434/v1"
export LLM_API_KEY="ollama"
export LLM_MODEL="llama3.1:8b"

# Groq
export LLM_BASE_URL="https://api.groq.com/openai/v1"
export LLM_API_KEY="your-groq-key"
export LLM_MODEL="llama-3.3-70b-versatile"
```

## Files Created

```
memory/
  observations.md          # The main observation log (loaded at startup)
  observation-backups/     # Reflector backups (last 10 kept)
  .observer-last-run       # Timestamp of last observer run
  .observer-last-hash      # Dedup hash of last processed messages
logs/
  observer.log
  reflector.log
  session-recovery.log
  observer-watcher.log
```

## Cron Jobs

The setup script creates these OpenClaw cron jobs:

| Job | Schedule | Description |
|-----|----------|-------------|
| `memory-observer` | Every 15 min | Compress recent conversation |
| `memory-reflector` | Hourly | Consolidate if observations are large |

## Reactive Watcher (Linux only)

The reactive watcher uses `inotifywait` to detect session activity and trigger the observer faster than cron alone. Requires Linux with `inotify-tools` installed.

```bash
# Install inotify-tools (Debian/Ubuntu)
sudo apt install inotify-tools

# Check watcher status
systemctl --user status total-recall-watcher

# View logs
journalctl --user -u total-recall-watcher -f
```

## Cost

Using the default free models via OpenRouter:
- ~$0.00/month (using free-tier models) for typical usage (observer + reflector)
- ~15-30 cron runs/day, each processing a few hundred tokens
- All default models are free tier models

## Model Notes

The scripts include defensive handling for both `.content` and `.reasoning` fields in API responses. While most models return content in the standard `.content` field, the fallback to `.reasoning` ensures compatibility with models that may use different response formats.

## How It Works (Technical)

### Observer
1. Finds recently modified session JSONL files
2. Filters out subagent/cron sessions
3. Extracts user + assistant messages from the lookback window
4. Deduplicates using MD5 hash comparison
5. Sends to LLM with the observer prompt (priority-based compression)
6. Appends result to `observations.md`
7. If observations exceed the word threshold, triggers reflector

### Reflector
1. Backs up current observations
2. Sends entire log to LLM with consolidation instructions
3. Validates output is shorter than input (sanity check)
4. Replaces observations with consolidated version
5. Cleans old backups (keeps last 10)

### Session Recovery
1. Runs at every `/new` or `/reset`
2. Hashes recent lines of the last session file
3. Compares against stored hash from last observer run
4. If mismatch: runs observer in recovery mode (4-hour lookback)
5. Fallback: raw message extraction if observer fails

### Reactive Watcher
1. Uses `inotifywait` to monitor session directory
2. Counts JSONL writes to main session files only
3. After 40+ lines: triggers observer (with 5-min cooldown)
4. Resets counter when cron/external observer runs are detected

## Customizing the Prompts

The observer and reflector system prompts are in `prompts/`:
- `prompts/observer-system.txt` — controls how conversations are compressed
- `prompts/reflector-system.txt` — controls how observations are consolidated

Edit these to match your agent's personality and priorities.

---

## Dream Cycle

The Dream Cycle is an optional nightly agent that runs after hours to consolidate `observations.md`. It archives stale items and adds semantic hooks so nothing useful is actually lost. Context stays lean; everything remains findable.

### What It Does

- Classifies every observation by impact (critical / high / medium / low / minimal) and age
- Archives items that have passed their relevance threshold
- Adds a semantic hook for each archived item (specific keywords + archive reference)
- Validates the result and rolls back automatically if something goes wrong

### Features

**Multi-Hook Retrieval** — 4-5 alternative search phrasings per archived item. Searches using different words than the original still find the memory.

**Confidence Scoring** — every observation gets a confidence score (0.0-1.0) and source type (`explicit`, `implicit`, `inference`, `weak`, `uncertain`). High-confidence items are preserved longer; low-confidence items are archived sooner.

**Memory Type System** — 7 types with per-type TTLs: `event` (14d), `fact` (90d), `preference` (180d), `goal` (365d), `habit` (365d), `rule` (never), `context` (30d). Embedded as invisible HTML metadata comments in `observations.md`.

**Observation Chunking** — clusters of 3+ related observations are compressed into single summary entries. Source observations are archived; a chunk hook replaces them. Achieves up to 75% token reduction.

**Importance Decay** — per-type daily decay applied to importance scores before each archival decision. Items that decay below the archive threshold are queued for removal. Rates: `event` (-0.5/day), `fact` (-0.1/day), `preference` (-0.02/day), `rule`/`habit`/`goal` (no decay).

**Pattern Promotion** — scans recent dream logs for recurring themes (3+ occurrences across 3+ separate days). Writes promotion proposals to `memory/dream-staging/` for human review. Use `staging-review.sh` to list, show, approve, or reject proposals. The `context` type is never promoted automatically.

### Setup

1. Run `bash skills/total-recall/scripts/setup.sh` — creates Dream Cycle directories automatically.

2. Add the nightly cron job as a full agent turn:
   ```
   # Dream Cycle — nightly (3am recommended; adjust to your timezone)
   # The dream cycle runs as a full agent turn — NOT as a direct bash call.
   # dream-cycle.sh is a file operations helper called BY the agent, not the entry point.
   #
   # 0 3 * * * bash -c 'source ~/.openclaw/shared/secrets/openclaw-secrets.env \
   #   && openclaw agent --agent main \
   #   --message "Run the Total Recall Dream Cycle. Follow the instructions in \
   # $WORKSPACE/skills/total-recall/prompts/dream-cycle-prompt.md exactly. \
   # Use READ_ONLY_MODE=false and DREAM_PHASE=1." \
   #   --json >> $WORKSPACE/logs/dream-cycle.log 2>&1'
   ```

3. Configure your cron agent using `prompts/dream-cycle-prompt.md` as the system prompt. Recommended models: Claude Sonnet for the Dreamer (analysis + decisions), DeepSeek v3.2 for the Observer (cheap, fast).

4. Start with `READ_ONLY_MODE=true` for the first few nights. Check `memory/dream-logs/` after each run to verify what it would have archived.

5. Switch to `READ_ONLY_MODE=false` once satisfied.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DREAM_TOKEN_TARGET` | `8000` | Token target for observations.md after consolidation |
| `READ_ONLY_MODE` | `false` | Set `true` for dry-run analysis without writes |

### Files

| File | Description |
|------|-------------|
| `scripts/dream-cycle.sh` | Shell helper called **by the agent** (not a standalone runner): preflight, archive, update-observations, write-log, write-metrics, validate, rollback |
| `prompts/dream-cycle-prompt.md` | Agent prompt for the nightly Dream Cycle run |
| `dream-cycle/README.md` | Dream Cycle quick reference |
| `schemas/observation-format.md` | Extended observation metadata format |

### Directories Created

```
memory/
  archive/
    observations/        # Archived items (one .md file per night)
    chunks/              # Chunked observation groups
  dream-logs/            # Nightly run reports
  dream-staging/         # Pattern promotion proposals awaiting human review
  .dream-backups/        # Pre-run safety backups
research/
  dream-cycle-metrics/
    daily/               # JSON metrics per night
```

---

## Troubleshooting

**Observer not running?**
- Check `logs/observer.log` for errors
- Verify `OPENROUTER_API_KEY` is set and valid
- Confirm cron is active: `crontab -l`

**Observations not being loaded at session start?**
- Ensure your agent's startup instructions include reading `memory/observations.md`
- Check `MEMORY_DIR` points to the right location

**Reactive watcher not triggering (Linux)?**
- Run `systemctl --user status total-recall-watcher`
- Check `inotify-tools` is installed: `which inotifywait`
- View watcher logs: `journalctl --user -u total-recall-watcher -f`

**Dream Cycle archiving too aggressively?**
- Enable `READ_ONLY_MODE=true` and review dream logs before going live
- Adjust `DREAM_TOKEN_TARGET` upward to archive less per run

**Dream Cycle not archiving enough?**
- Lower `DREAM_TOKEN_TARGET` to trigger more aggressive consolidation

---

## Inspired By

This system is inspired by how human memory works during sleep — the hippocampus (observer) captures experiences, and during sleep consolidation (reflector), important memories are strengthened while noise is discarded.

Read more: [Your AI Has an Attention Problem](https://gavlahh.substack.com/p/your-ai-has-an-attention-problem)

*"Get your ass to Mars." — Well, get your agent's memory to work.*
