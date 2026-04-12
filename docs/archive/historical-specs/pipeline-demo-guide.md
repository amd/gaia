# Pipeline Demo Guide

How to run and test the GAIA recursive iterative pipeline with real Lemonade LLM backend.

## Quick Start

### Prerequisites
1. Install GAIA: `uv pip install -e ".[dev]"`
2. Start Lemonade server: `lemonade-server serve`
3. Verify model available: `lemonade models` (should show Qwen3-0.6B-GGUF)

## Running the Demos

### 1. Quick Verify (Stub Mode — No Lemonade Required)

Test the pipeline structure without an LLM:
```bash
python examples/pipeline_demo.py --goal "Build a REST API" --stub
```
Expected output: Agent names, iteration count, placeholder artifact text.

### 2. Live LLM Demo (Requires Lemonade Running)

```bash
# Start Lemonade first
lemonade-server serve

# Run with default small model
python examples/pipeline_demo.py --goal "Build a REST API with FastAPI" --model Qwen3-0.6B-GGUF

# Run Lemonade-specific demo
python examples/pipeline_with_lemonade.py "Write a Python script to analyze CSV files"

# Use different template
python examples/pipeline_demo.py --goal "Design a microservices architecture" --template enterprise --model Qwen3-0.6B-GGUF
```

### 3. Template Reference

| Template | Agents | Max Iterations | Use Case |
|----------|--------|----------------|----------|
| generic | planning → dev (parallel) → review (parallel) | 10 | General tasks |
| rapid | planning → dev | 5 | Quick prototypes |
| enterprise | planning → 3 specialist devs (parallel) → review → security (parallel) | 15 | Production features |

## Running Tests

### Unit Tests (No Lemonade Required)
```bash
python -m pytest tests/unit/ -v
```

### Pipeline Tests with Lemonade
```bash
# Start Lemonade first, then:
python -m pytest tests/ -k "pipeline" --model-id Qwen3-0.6B-GGUF -v

# Run specific pipeline test
python -m pytest tests/unit/test_pipeline_engine.py -v
```

### Stub Mode Tests (CI-Safe)
```bash
python -m pytest tests/unit/ -v  # All unit tests use stubs
```

## Understanding Real vs Stub Mode

The pipeline has two execution paths:

**Real Mode** (Lemonade running):
- `ConfigurableAgent._run_agent_loop()` calls `self.chat.send_messages()`
- Returns actual LLM-generated code/text
- Requires `lemonade-server serve` to be running

**Stub Mode** (Lemonade NOT running):
- Returns `"Agent {id} processed: {prompt}"` placeholder
- Useful for testing pipeline structure without GPU

To force real mode: ensure `lemonade-server serve` is running before executing demos.

## Model Selection

The pipeline uses a 4-level priority chain for model selection:
1. Per-agent `model_id` in `config/agents/*.yaml`
2. Engine-level `--model` CLI flag
3. Template `default_model` in `config/pipeline_templates/*.yaml`
4. Hardcoded fallback: `Qwen3-0.6B-GGUF`

**Recommended for local development:** `Qwen3-0.6B-GGUF` (small, fast, fits on NPU)

## Troubleshooting

**"Agent X processed: prompt" in output** — Lemonade is not running. Start with `lemonade-server serve`.

**"Lemonade server not running"** — Check `lemonade-server serve` is started and accessible at `http://localhost:11434`.

**ModuleNotFoundError: fastapi** — Run `uv pip install -e ".[ui]"` for UI extras.

**Pipeline loops too many times** — Reduce `max_iterations` in your template YAML or use `--template rapid`.
