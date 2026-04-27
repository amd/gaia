# GAIA Inbox Zero Agent

Production-ready email triage, categorization, and inbox management agent. Processes real Gmail data from MBOX takeout files using LLM-powered classification.

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env with your MBOX path and LLM endpoint

# 3. Run batch classifier
python -m gaia_inbox_zero.cli.batch_classifier --limit 100 --batch-size 20

# 4. Run tests
pip install pytest
pytest tests/ -v
```

## Architecture

```
gaia-inbox-zero-agent/
├── agent/                  # GAIA agent module (requires GAIA framework)
│   ├── inbox_zero.py       # Main InboxZeroAgent class
│   ├── config.py           # Centralized configuration
│   └── classifiers.py      # Email classification engine
├── data/
│   └── email_loader.py     # MBOX email data loader
├── cli/
│   └── batch_classifier.py # Standalone batch classifier CLI
├── schema/
│   └── result_schema.py    # Result dataclasses and serialization
├── automations/
│   └── inbox-zero-helper.yaml  # OpenClaw ClawFlow automation
└── tests/                  # Comprehensive test suite
```

### Data Flow

```
MBOX File ──→ email_loader.py ──→ batch_classifier.py ──→ LLM API ──→ Results JSON
                      │                                      │
                      │         ┌────────────────────────────┘
                      │         │
                      ▼         ▼
              InboxZeroAgent (GAIA) ──→ Tools: fetch, classify, archive, group
```

### Components

| Component | Description |
|-----------|-------------|
| **email_loader** | Parses Gmail MBOX takeout files into structured dicts |
| **classifiers** | Heuristic category assignment + LLM classification |
| **batch_classifier** | CLI tool: processes emails in batches via LLM API |
| **InboxZeroAgent** | GAIA framework agent with email triage tools |
| **result_schema** | Dataclasses for benchmark results and metrics |

## Configuration

All configuration via environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `MBOX_PATH` | Path to Gmail MBOX takeout | (required) |
| `LEMONADE_URL` | OpenAI-compatible LLM endpoint | `http://localhost:8001/v1/chat/completions` |
| `ANTHROPIC_URL` | Anthropic Messages API endpoint | `https://api.anthropic.com/v1/messages` |
| `ANTHROPIC_API_KEY` | Anthropic API key | (required for anthropic provider) |
| `GAIA_MODEL` | Default model for GAIA agents | `Qwen3.5-35B-A3B-GGUF` |

## Email Categories

| Category | Description | Action |
|----------|-------------|--------|
| URGENT | Time-sensitive, response within hours | Respond immediately |
| NEEDS_RESPONSE | Requires action but not urgent | Draft response |
| FYI | Informational, no action needed | Review/Archive |
| PROMOTIONAL | Marketing, newsletters, deals | Archive |
| PERSONAL | Friends, family, non-work | Review |

## Usage

### Batch Classifier CLI

```bash
# Process 100 emails in batches of 20
python -m gaia_inbox_zero.cli.batch_classifier --limit 100 --batch-size 20

# Use custom MBOX path
python -m gaia_inbox_zero.cli.batch_classifier --mbox-path /path/to/mail.mbox

# Use Anthropic provider
# Set ANTHROPIC_API_KEY in .env, then use --provider anthropic
python -m gaia_inbox_zero.cli.batch_classifier --provider anthropic
```

### Programmatic Usage

```python
from gaia_inbox_zero.data.email_loader import load_mbox, count_mbox
from gaia_inbox_zero.schema.result_schema import TaskResult, BatchMetrics

# Load emails
emails = load_mbox(limit=20, reverse=True)
total = count_mbox()

# Use the schema
result = TaskResult(...)
output = result.to_dict()
```

### GAIA Agent Integration

```python
from gaia_inbox_zero.agent.inbox_zero import InboxZeroAgent

agent = InboxZeroAgent(
    mbox_path="/path/to/mail.mbox",
    model_id="your-model",
)

# Process in batches
results = agent.process_in_batches(
    batch_size=20,
    total_emails=100,
    timeout=1200,
)
```

## LLM Providers

The batch classifier supports two providers:

- **Lemonade** (default): OpenAI-compatible endpoint, typically local AMD ROCm inference
- **Anthropic**: Anthropic Messages API (Claude)

Set the provider via `--provider` flag or configure the appropriate URL in `.env`.

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_email_loader.py -v

# Run with coverage
pytest tests/ --cov=gaia_inbox_zero --cov-report=html
```

## License

MIT
