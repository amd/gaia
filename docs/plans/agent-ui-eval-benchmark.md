# GAIA Agent Eval — Benchmarking Plan

**Date:** 2026-03-17
**Status:** In Progress — Fix Phase complete, post-restart re-eval running
**Priority:** High
**Last Updated:** 2026-03-20

---

## Current State (2026-03-20)

### Benchmark Run Complete
All 23 scenarios executed. **17/23 PASS (73.9%), avg 7.93/10.**

| Category | PASS | FAIL | Avg |
|----------|------|------|-----|
| context_retention | 5/5 | 0 | 9.23 |
| adversarial | 3/3 | 0 | 8.10 |
| personality | 1/2 | 1 | 8.53 |
| tool_selection | 2/3 | 1 | 7.16 |
| error_recovery | 2/3 | 1 | 7.58 |
| rag_quality | 2/6 | 4 | 6.96 |

### Fixes Applied & Validated
| Fix | File | Before | After |
|-----|------|--------|-------|
| Fuzzy basename fallback in `query_specific_file` | `rag_tools.py` | negation_handling 4.62 | 8.10 ✅ |
| Verbosity rule in system prompt | `agent.py` | concise_response 7.15 | re-eval in progress |
| Session isolation in `_resolve_rag_paths` | `_chat_helpers.py` | cross_section_rag 6.67 | 9.27 ✅ |

### Still Failing (deeper fixes needed)
| Scenario | Score | Root Cause |
|----------|-------|------------|
| smart_discovery | 2.80 | `search_file` doesn't scan project subdirs |
| table_extraction | 5.17 | CSV chunked into only 2 RAG chunks |
| search_empty_fallback | 5.32 | Agent doesn't fall back when search returns empty |

### Important Constraint
**DO NOT call `delete_session`** in any eval task — conversations must be preserved.
**Always pass `session_id` to `index_document`** — required for Fix 3 session isolation.

---

## Executive Summary

Build an **agentic eval benchmarking framework** that validates the GAIA agent's reliability
and quality by using a **Python CLI** (`gaia eval agent`) that invokes **Claude Code** (`claude -p`)
as a subprocess to both **simulate realistic users** and **judge agent responses**. The eval
drives multi-turn conversations against the live Agent UI via its **MCP server**, captures full
execution traces (tool calls, reasoning, answers), and produces a scorecard that Claude Code
can read and act on to iteratively improve agent quality.

```
┌──────────────────────────────────────────────────────────────────────┐
│  $ gaia eval agent [--fix]                                           │
│                                                                      │
│  Python runner (src/gaia/eval/runner.py)                             │
│    │                                                                 │
│    │  For each scenario (sequential):                                │
│    │    ┌──────────────────────────────────────────────────────────┐ │
│    │    │ subprocess: claude -p "{prompt}"                         │ │
│    │    │   --output-format json  --json-schema "{schema}"        │ │
│    │    │   --mcp-config eval/mcp-config.json                     │ │
│    │    │   --strict-mcp-config  --model claude-sonnet-4-6        │ │
│    │    │   --permission-mode auto  --max-budget-usd 0.50         │ │
│    │    │                                                         │ │
│    │    │ Claude Code simulates user + judges agent:              │ │
│    │    │   MCP: create_session → index_document → send_message   │ │
│    │    │   Returns: structured JSON result to stdout              │ │
│    │    └─────────────────────────┬────────────────────────────────┘ │
│    │                              ▼                                  │
│    │    Python: parse JSON, write trace, track cost                  │
│    │                                                                 │
│    │  Agent UI (:4200) ──▶ Local LLM (Lemonade/Qwen3)              │
│    │                                                                 │
│    ├── Aggregate → scorecard.json + summary.md                       │
│    │                                                                 │
│    └── [--fix] Claude Code fixes failures → re-eval → repeat        │
└──────────────────────────────────────────────────────────────────────┘
```

### Core Principles

1. **No mocking.** Always test against the real local LLM (Lemonade + Qwen3). The eval
   must exercise the actual system users will use. No fake responses, no canned data,
   no test doubles for the LLM.
2. **Cloud LLM as judge.** Claude evaluates every agent response — nuanced understanding
   of intent, not brittle keyword matching.
3. **Cloud LLM as user simulator.** Claude generates realistic, adaptive user messages —
   not canned scripts.
4. **Agentic.** The eval is driven by Claude Code tasks, not static test runners. The eval
   agent reasons about what to test, adapts follow-ups, and diagnoses root causes.
5. **File-based results.** All eval output written to files in the shared workspace.
   Never depend on terminal output buffers.
6. **Reproducible corpus.** Synthetic documents generated with a fixed random seed —
   running `generate_all.py` twice produces identical documents with identical facts.

### Why This Approach

| Decision | Rationale |
|----------|-----------|
| **Python CLI** (`gaia eval agent`) | Deterministic orchestration, crash recovery, cost tracking, scriptable |
| **Claude Code** (`claude -p`) | Full codebase context, native MCP tools, adaptive reasoning — best possible user simulation + judging |
| **Agent UI MCP server** | Already exists (17 tools), returns full execution traces, syncs with browser UI |
| **Eval webapp** | Dashboard to view results, trigger runs, compare baselines — not just read-only reports |
| **Real local LLM only** | Tests the actual system users will use — no mocks |
| **Synthetic data corpus** | Documents with known, machine-verifiable facts for ground truth |
| **Cost tracking** | `--max-budget-usd` per scenario + accumulated totals |

---

## 1. Architecture

### 1.1 Python CLI + Claude Code Subprocess

The eval is a Python CLI command (`gaia eval agent`) that invokes Claude Code in print mode
(`claude -p`) for each scenario. Python handles orchestration; Claude Code handles reasoning.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Python CLI: gaia eval agent                                            │
│  (src/gaia/eval/runner.py)                                       │
│                                                                      │
│  For each scenario:                                                  │
│    ┌──────────────────────────────────────────────────────────────┐  │
│    │  subprocess: claude -p "{scenario_prompt}"                   │  │
│    │    --output-format json                                      │  │
│    │    --mcp-config eval/mcp-config.json  (Agent UI MCP)        │  │
│    │    --model claude-sonnet-4-6                                 │  │
│    │    --json-schema "{scorecard_schema}"                        │  │
│    │    --max-budget-usd 0.50                                    │  │
│    │    --permission-mode auto                                   │  │
│    │                                                              │  │
│    │  Claude Code (simulator + judge):                            │  │
│    │    → Reads scenario YAML + corpus manifest                   │  │
│    │    → Calls MCP: create_session, index_document, send_message │  │
│    │    → Generates realistic user messages                       │  │
│    │    → Captures agent response + traces                        │  │
│    │    → Judges each turn (scores, pass/fail, root cause)        │  │
│    │    → Returns structured JSON to stdout                       │  │
│    └──────────────────────┬───────────────────────────────────────┘  │
│                           │ JSON result                              │
│    Python collects:       ▼                                          │
│    - Parse JSON result                                               │
│    - Accumulate costs                                                │
│    - Write trace file                                                │
│    - Crash recovery (resume if interrupted)                          │
│    - Aggregate scorecard after all scenarios                         │
│                                                                      │
│  Agent UI Backend (:4200) ──▶ Local LLM (Lemonade/Qwen3)           │
│    (accessed by Claude Code via MCP tools)                           │
└──────────────────────────────────────────────────────────────────────┘
```

**Why this architecture:**

| Layer | Responsibility | Why This Tool |
|-------|---------------|---------------|
| **Python CLI** | Orchestration, cost tracking, crash recovery, aggregation | Deterministic loops, file I/O, `gaia eval agent` command |
| **Claude Code** (`claude -p`) | User simulation, judging, MCP interaction | Full codebase context, native MCP tools, adaptive reasoning |
| **Agent UI MCP** | Interface to GAIA agent | Already exists (17 tools), returns full traces |
| **Eval Webapp** | Dashboard, run control, regression comparison | Visual results + ability to trigger runs |

**Key Claude Code CLI flags:**

| Flag | Purpose |
|------|---------|
| `-p "prompt"` | Non-interactive print mode — outputs result to stdout |
| `--output-format json` | Structured JSON response for Python to parse |
| `--mcp-config eval/mcp-config.json` | Loads Agent UI MCP server (17 tools) |
| `--json-schema '{...}'` | Forces output to match scorecard schema exactly |
| `--model claude-sonnet-4-6` | Specifies eval model |
| `--max-budget-usd 0.50` | Caps cost per scenario invocation |
| `--permission-mode auto` | Skips permission prompts for unattended runs |
| `--strict-mcp-config` | Only uses MCP servers from `--mcp-config`, ignores user/project configs |
| `--system-prompt "..."` | Injects eval agent system prompt (personas, scoring, etc.) |

### 1.2 Data Flow for One Scenario

```
1. Python runner loads scenario YAML and builds prompt
2. Python shells out:
   claude -p "{prompt}" --output-format json --mcp-config eval/mcp-config.json ...
3. Claude Code (subprocess) executes:
   a. Reads scenario file + corpus manifest (via file system)
   b. Calls MCP: create_session("Eval: cross_turn_file_recall")
   c. Calls MCP: index_document(absolute_path_to_corpus_doc)
   d. Generates user message based on scenario objective + persona
   e. Calls MCP: send_message(session_id, user_message)
      → Returns: {content, agent_steps, event_log}
   f. Judges agent response against ground truth (scores 0-10)
   g. Generates next user message adapting to agent's response
   h. Repeats for all turns
   i. Calls MCP: get_messages(session_id) for full traces
   j. Calls MCP: delete_session(session_id)
   k. Returns structured JSON result to stdout
4. Python runner parses JSON result
5. Python runner writes trace file to eval/results/{run_id}/traces/
6. Python runner accumulates cost from Claude Code's output
7. Repeat for next scenario
8. Python runner aggregates all trace files into scorecard.json + summary.md
```

### 1.3 Replacing the Existing Eval Framework

The existing eval framework (`src/gaia/eval/`, ~9,200 lines) was built for LLM-only
evaluation (RAG Q&A, summarization, code fixing). It is **replaced entirely** by this
agent eval framework. No backwards compatibility required.

**What gets absorbed into the new framework:**

| Old Component | Disposition |
|--------------|------------|
| `ClaudeClient` (`claude.py`) | **Keep** — Anthropic SDK wrapper with cost tracking. Used if Python needs direct API calls. |
| `config.py` (MODEL_PRICING) | **Keep** — model pricing constants. |
| `Evaluator` (`eval.py`) | **Replace** — new scoring dimensions (7 vs 4), new pass/fail logic, Claude Code as judge instead of API calls. Absorb `calculate_similarity()` into new framework. |
| `GroundTruthGenerator` (`groundtruth.py`) | **Replace** — new corpus generator with manifest.json format. |
| `BatchExperimentRunner` (`batch_experiment.py`) | **Replace** — new `AgentEvalRunner` with `claude -p` subprocess pattern. Absorb crash recovery pattern. |
| `PDFDocumentGenerator` | **Keep** — reuse for corpus PDF generation. |
| `TranscriptGenerator`, `EmailGenerator` | **Remove** — not needed for agent eval. |
| `fix_code_testbench/` | **Remove** — replaced by agent eval scenarios. |
| `webapp/` | **Rewrite** — new visualization for agent eval results (scenario detail, comparison, score heatmaps). |
| CLI commands (`gaia eval`, `gaia groundtruth`, `gaia report`) | **Replace** — single `gaia eval agent` command with `--fix`, `--audit-only`, `--generate-corpus` flags. |

**What's new:**

| Component | Purpose |
|-----------|---------|
| **Agent UI MCP server** | `src/gaia/mcp/servers/agent_ui_mcp.py` — 17 tools for driving conversations |
| **`claude -p` subprocess** | Claude Code in print mode — simulation + judging with full codebase context |
| **Scenario YAML library** | 23 scenarios across 6 categories |
| **Synthetic corpus** | 18+ documents with machine-verifiable facts |
| **Architecture audit** | Deterministic checks on conversation history, truncation, agent persistence |
| **Fix mode** | Automated eval→fix→re-eval loop via Claude Code |

### 1.4 MCP Server Configuration

The `claude -p` subprocess needs the Agent UI MCP server. Create `eval/mcp-config.json`:

```json
{
  "mcpServers": {
    "gaia-agent-ui": {
      "command": "uv",
      "args": ["run", "python", "-m", "gaia.mcp.servers.agent_ui_mcp", "--stdio"],
      "env": {}
    }
  }
}
```

Passed to each subprocess via `--mcp-config eval/mcp-config.json --strict-mcp-config`
(strict mode prevents user's other MCP servers from interfering with eval).

---

## 2. Claude Code Eval Agent — Prompt Design

### 2.1 Eval Agent Prompt

This prompt is passed to `claude -p` by the Python runner for each scenario:

```
You are the GAIA Eval Agent. Your job is to test the GAIA Agent UI by acting as a
realistic user, then judging the agent's responses.

You have access to the Agent UI MCP server. Use its tools to drive conversations.

## YOUR TASK

Run the eval scenario defined in: eval/scenarios/{scenario_file}
Use the ground truth from: eval/corpus/manifest.json
Return your result as JSON to stdout (the Python runner writes files).

## PHASE 1: SETUP
1. Read the scenario file to understand the test
2. Read the corpus manifest to get ground truth facts
3. Call system_status() to verify GAIA is running. If it returns an error, abort
   and write a result with status "INFRA_ERROR".
4. Call create_session() with title "Eval: {scenario_name}"
5. If scenario requires documents, call index_document() for each.
   Use ABSOLUTE file paths (resolve from workspace root).
   If index_document fails (error in response, chunk_count=0), log the error
   in the result file and mark the scenario as "SETUP_ERROR" — do NOT proceed
   with chat turns since RAG won't work without indexed documents.

## PHASE 2: SIMULATE USER
For each turn in the scenario:
1. Generate a realistic user message based on:
   - The scenario's turn objective
   - The persona (see PERSONAS below)
   - The agent's previous responses (adapt naturally)
2. Call send_message(session_id, your_message)
3. Record the full response: content, agent_steps, event_log

PERSONAS:
- casual_user: Short messages, uses pronouns ("that file", "the one you showed me"),
  occasionally vague. Tests context retention and ambiguity handling.
- power_user: Precise requests, names specific files, multi-step asks.
  Tests tool orchestration and efficiency.
- confused_user: Wrong terminology, unclear requests, then self-corrects.
  Tests error recovery and clarification.
- adversarial_user: Edge cases, rapid topic switches, impossible requests.
  Tests robustness and hallucination resistance.
- data_analyst: Asks about numbers, comparisons, aggregations.
  Tests table extraction and data accuracy.

Rules for generating user messages:
- Sound natural — typos OK, overly formal is not
- Use pronouns and references to test context retention
- If agent asked a clarifying question, answer it naturally
- If agent got something wrong, push back
- Stay in character for the assigned persona

## PHASE 3: JUDGE EACH TURN
After each turn, evaluate the agent's response.

Score each dimension 0-10:
- correctness (weight 25%): Factual accuracy vs ground truth
- tool_selection (weight 20%): Right tools chosen, no unnecessary calls
- context_retention (weight 20%): Used info from previous turns appropriately
- completeness (weight 15%): Fully answered the question
- efficiency (weight 10%): Steps taken vs optimal path
- personality (weight 5%): GAIA voice (witty, direct, no sycophancy)
- error_recovery (weight 5%): Tried alternatives when tools failed

Compute overall_score as the weighted average:
  overall = correctness*0.25 + tool_selection*0.20 + context_retention*0.20
          + completeness*0.15 + efficiency*0.10 + personality*0.05
          + error_recovery*0.05

Determine pass/fail:
- PASS if overall_score >= 6.0 AND no critical failure
- FAIL otherwise

Classify failure (if any):
- wrong_answer, hallucination, context_blindness, wrong_tool,
  gave_up, tool_loop, no_fallback, personality_violation

## PHASE 4: COLLECT FULL TRACES
After all turns, call get_messages(session_id) to retrieve the full conversation
with agent_steps from the database. The streaming send_message truncates some data
(thinking to 150 chars, tool args to 200 chars). get_messages gives you the
persisted version with more detail. Use the fuller data for Phase 5.

## PHASE 5: SCENARIO JUDGMENT
Using the full traces from Phase 4, evaluate the scenario holistically:
- Did the agent complete the overall task?
- Was the conversation coherent across turns?
- What is the root cause of any failures?
- What specific code changes would fix the issues?

## PHASE 6: RETURN RESULT
Return your evaluation as JSON (the Python runner captures stdout and writes files).
Your response MUST be a single JSON object with this structure:
{
  "scenario_id": "...",
  "status": "PASS|FAIL|BLOCKED_BY_ARCHITECTURE|INFRA_ERROR|SETUP_ERROR|TIMEOUT|ERRORED",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "...",
      "agent_response": "...",
      "agent_tools": ["tool1", "tool2"],
      "event_log": ["[thinking] ...", "[tool] ..."],
      "scores": {"correctness": 0-10, "tool_selection": 0-10, ...},
      "pass": true/false,
      "failure_category": null or "category_name",
      "reasoning": "..."
    }
  ],
  "root_cause": null or "description",
  "recommended_fix": null or {"target": "...", "file": "...", "description": "..."},
  "cost_estimate": {"turns": N, "estimated_usd": X.XX}
}

Do NOT write files yourself — the Python runner handles file output.

## PHASE 7: CLEANUP
Call delete_session(session_id) to clean up

## COST TRACKING
Track your own token usage. At the end, report:
- Estimated total tokens used (input + output)
- Estimated cost based on your model's pricing
```

### 2.2 Fixer Prompt (used by `--fix` mode)

The fixer prompt is detailed in §11.2. It is passed to a separate `claude -p`
subprocess after the eval phase completes.

---

## 3. Scoring Dimensions

| Dimension | Weight | What It Measures | Score Guide |
|-----------|--------|------------------|-------------|
| `correctness` | 25% | Factual accuracy vs ground truth | 10=exact match, 7=mostly right, 4=partially wrong, 0=completely wrong/hallucinated |
| `tool_selection` | 20% | Right tool for the job | 10=optimal tools, 7=correct but extra calls, 4=wrong tool but recovered, 0=completely wrong tools |
| `context_retention` | 20% | Uses info from previous turns | 10=perfect recall, 7=used most context, 4=missed key info, 0=ignored previous turns entirely |
| `completeness` | 15% | Fully answers the question | 10=complete answer, 7=mostly complete, 4=partial, 0=didn't answer |
| `efficiency` | 10% | Steps taken vs optimal | 10=optimal path, 7=1-2 extra steps, 4=many unnecessary steps, 0=tool loop |
| `personality` | 5% | GAIA voice | 10=witty+direct, 7=neutral, 4=generic AI, 0=sycophantic |
| `error_recovery` | 5% | Handles tool failures | 10=graceful recovery, 7=recovered after retry, 4=partial recovery, 0=gave up |

**Pass threshold:** overall_score >= 6 AND no critical failure category

**Failure categories:**
- `wrong_answer` — Factually incorrect response
- `hallucination` — Claims not supported by any document or context
- `context_blindness` — Ignores information from previous turns
- `wrong_tool` — Uses clearly inappropriate tool for the task
- `gave_up` — Stops trying after a tool returns empty/error
- `tool_loop` — Calls the same tool repeatedly without progress
- `no_fallback` — First approach fails, doesn't try alternatives
- `personality_violation` — Sycophantic, verbose, or off-brand response

---

## 4. Synthetic Data Corpus

Every test document is **synthetic** with **machine-verifiable facts** embedded at known locations.

### 4.1 Corpus Structure

```
eval/
├── corpus/
│   ├── generator/
│   │   ├── __init__.py
│   │   ├── generate_all.py      # Master script — generates entire corpus
│   │   ├── gen_pdf.py           # PDF generator (reportlab or fpdf2)
│   │   ├── gen_csv.py           # CSV generator (synthetic tabular data)
│   │   ├── gen_markdown.py      # Markdown document generator
│   │   ├── gen_html.py          # HTML report generator
│   │   ├── gen_code.py          # Python source file generator
│   │   ├── gen_text.py          # Plain text document generator
│   │   └── gen_adversarial.py   # Edge case documents
│   ├── manifest.json            # Master index: documents + facts + metadata
│   ├── documents/               # Generated documents (gitignored, regenerable)
│   └── adversarial/             # Edge case documents (gitignored, regenerable)
```

### 4.2 Document Manifest

```json
{
  "generated_at": "2026-03-17T10:00:00Z",
  "total_documents": 18,
  "total_facts": 108,
  "documents": [
    {
      "id": "acme_q3_report",
      "filename": "acme_quarterly_report.pdf",
      "format": "pdf",
      "pages": 8,
      "size_category": "medium",
      "domain": "finance",
      "description": "Acme Corp Q3 2025 quarterly financial report with revenue tables, CEO letter, and projections",
      "generator": "gen_pdf.py",
      "facts": [
        {
          "id": "q3_revenue",
          "question": "What was Acme Corp's Q3 2025 revenue?",
          "answer": "$14.2 million",
          "location": "Page 3, Revenue Summary table",
          "difficulty": "easy",
          "category": "table_extraction",
          "keywords": ["revenue", "14.2", "Q3"]
        },
        {
          "id": "yoy_growth",
          "question": "What was the year-over-year revenue growth?",
          "answer": "23% increase from Q3 2024's $11.5 million",
          "location": "Page 3, paragraph below table",
          "difficulty": "medium",
          "category": "cross_reference"
        },
        {
          "id": "employee_count",
          "question": "How many employees does Acme have?",
          "answer": null,
          "difficulty": "hard",
          "category": "hallucination_resistance",
          "note": "NOT in the document — agent must say it doesn't know"
        },
        {
          "id": "ceo_outlook",
          "question": "What is the CEO's outlook for Q4?",
          "answer": "Projected 15-18% growth driven by enterprise segment expansion",
          "location": "Page 7, CEO Letter, paragraph 3",
          "difficulty": "medium",
          "category": "synthesis"
        }
      ]
    },
    {
      "id": "employee_handbook",
      "filename": "employee_handbook.md",
      "format": "markdown",
      "sections": 12,
      "size_category": "large",
      "domain": "hr_policy",
      "description": "Corporate employee handbook with PTO, benefits, remote work, conduct",
      "generator": "gen_markdown.py",
      "facts": [
        {
          "id": "pto_days",
          "question": "How many PTO days do first-year employees get?",
          "answer": "15 days",
          "location": "Section 4: Time Off Policy",
          "difficulty": "easy",
          "category": "direct_lookup"
        },
        {
          "id": "remote_work",
          "question": "What is the remote work policy?",
          "answer": "Up to 3 days/week with manager approval. Fully remote requires VP approval.",
          "location": "Section 7: Remote Work",
          "difficulty": "medium",
          "category": "multi_sentence"
        },
        {
          "id": "contractor_benefits",
          "question": "Are contractors eligible for health benefits?",
          "answer": "No — benefits are for full-time employees only",
          "location": "Section 5: Benefits",
          "difficulty": "hard",
          "category": "negation_handling"
        }
      ]
    },
    {
      "id": "sales_data",
      "filename": "sales_data_2025.csv",
      "format": "csv",
      "rows": 500,
      "columns": ["date", "product", "units", "unit_price", "revenue", "region", "salesperson"],
      "size_category": "medium",
      "domain": "sales",
      "description": "500 rows of sales transactions for Q1 2025",
      "generator": "gen_csv.py",
      "facts": [
        {
          "id": "top_product_march",
          "question": "What was the best-selling product in March 2025?",
          "answer": "Widget Pro X with 142 units and $28,400 revenue",
          "difficulty": "medium",
          "category": "aggregation"
        },
        {
          "id": "q1_total_revenue",
          "question": "What was total Q1 2025 revenue?",
          "answer": "$342,150",
          "difficulty": "medium",
          "category": "summation"
        },
        {
          "id": "top_salesperson",
          "question": "Who was the top salesperson by revenue?",
          "answer": "Sarah Chen with $67,200",
          "difficulty": "medium",
          "category": "aggregation"
        }
      ]
    },
    {
      "id": "product_comparison",
      "filename": "product_comparison.html",
      "format": "html",
      "size_category": "small",
      "domain": "product",
      "description": "HTML comparison of two software products with feature tables",
      "generator": "gen_html.py",
      "facts": [
        {
          "id": "price_difference",
          "question": "What is the price difference between the products?",
          "answer": "Product A: $49/month, Product B: $79/month — $30/month difference",
          "difficulty": "easy",
          "category": "comparison"
        }
      ]
    },
    {
      "id": "api_docs",
      "filename": "api_reference.py",
      "format": "python",
      "size_category": "small",
      "domain": "technical",
      "description": "Python source with docstrings documenting a REST API",
      "generator": "gen_code.py",
      "facts": [
        {
          "id": "auth_method",
          "question": "What authentication method does the API use?",
          "answer": "Bearer token via the Authorization header",
          "difficulty": "easy",
          "category": "code_comprehension"
        }
      ]
    },
    {
      "id": "meeting_notes",
      "filename": "meeting_notes_q3.txt",
      "format": "text",
      "size_category": "small",
      "domain": "general",
      "description": "Plain text meeting notes with action items and decisions",
      "generator": "gen_text.py",
      "facts": [
        {
          "id": "next_meeting",
          "question": "When is the next meeting?",
          "answer": "October 15, 2025 at 2:00 PM",
          "difficulty": "easy",
          "category": "direct_lookup"
        }
      ]
    }
  ],
  "adversarial_documents": [
    {
      "id": "empty_file",
      "filename": "empty.txt",
      "expected_behavior": "Agent reports file is empty"
    },
    {
      "id": "unicode_heavy",
      "filename": "unicode_test.txt",
      "expected_behavior": "No encoding errors"
    },
    {
      "id": "large_pdf",
      "filename": "large_report.pdf",
      "pages": 75,
      "facts": [
        {
          "id": "buried_fact",
          "question": "What was the compliance finding on page 52?",
          "answer": "Three minor non-conformities in supply chain documentation",
          "difficulty": "hard",
          "category": "deep_retrieval"
        }
      ]
    },
    {
      "id": "duplicate_content",
      "filename": "duplicate_sections.md",
      "expected_behavior": "Agent does not return duplicate chunks"
    }
  ]
}
```

### 4.3 Coverage Matrix

| Dimension | Variants | Purpose |
|-----------|----------|---------|
| **Format** | PDF, Markdown, TXT, CSV, HTML, Python, JSON | Different RAG extraction paths |
| **Size** | Small (&lt;50KB), Medium (50KB-1MB), Large (1-10MB), XL (&gt;10MB) | Indexing behavior, chunking |
| **Domain** | Finance, HR, sales, technical, medical, general | Vocabulary diversity |
| **Fact difficulty** | Easy (lookup), Medium (cross-reference), Hard (synthesis/negation) | Retrieval + reasoning depth |
| **Adversarial** | Empty, unicode, very large, duplicates | Edge case resilience |

**Target: 18-20 documents, 100+ verifiable facts, fully regenerable via `generate_all.py`.**

**Reproducibility:** All generators use a fixed random seed (`SEED=42` by default).
Running `generate_all.py` twice produces byte-identical documents with identical facts.
This ensures eval results are comparable across runs — the corpus is a constant, not a variable.

---

## 5. Architecture Audit (Deterministic, No LLM)

Before running scenarios, a deterministic audit inspects the agent's architecture
to identify structural limitations. This runs **without any LLM calls** — instant and free.

### 5.1 What It Checks

| Check | Source | Impact |
|-------|--------|--------|
| History pairs limit | `_MAX_HISTORY_PAIRS` in `_chat_helpers.py` | Max turns of context |
| Truncation limit | `_MAX_MSG_CHARS` in `_chat_helpers.py` | Whether file paths survive across turns |
| Tool results in history | Whether `agent_steps` are fed back to LLM | Cross-turn tool data availability |
| Agent persistence | Whether ChatAgent is recreated per message | Statefulness |
| Tool result truncation | `max_chars` in `_create_tool_message` | Large result preservation |

### 5.2 Output

```json
{
  "architecture_audit": {
    "history_pairs": 2,
    "max_msg_chars": 500,
    "tool_results_in_history": false,
    "agent_persistence": "stateless_per_message",
    "tool_result_truncation_chars": 2000,
    "blocked_scenarios": [
      {
        "scenario": "cross_turn_file_recall",
        "blocked_by": "tool_results_in_history=false",
        "explanation": "File paths from list_recent_files are in tool results, which are not passed to the LLM in the next turn."
      }
    ],
    "recommendations": [
      {
        "id": "include_tool_results",
        "impact": "critical",
        "file": "src/gaia/ui/_chat_helpers.py",
        "description": "Include tool result summaries in conversation history"
      },
      {
        "id": "increase_truncation",
        "impact": "high",
        "file": "src/gaia/ui/_chat_helpers.py",
        "description": "Increase _MAX_MSG_CHARS from 500 to 1500+"
      }
    ]
  }
}
```

### 5.3 BLOCKED vs FAILED

- **BLOCKED_BY_ARCHITECTURE**: Agent never received the data. Fix: code changes.
- **FAILED**: Agent received data but made bad decisions. Fix: prompt/tool descriptions.

---

## 6. Scenario Definitions

### 6.1 Format

```yaml
id: cross_turn_file_recall
name: "Cross-Turn File Recall"
category: context_retention
severity: critical
description: |
  User lists recent files, then asks to analyze one by name
  without providing the path. Agent must connect the dots.

persona: casual_user

setup:
  # Paths use ~ for home dir. Eval agent resolves to platform-appropriate absolute path.
  stage_files:
    - corpus_doc: product_comparison
      dest: "~/Downloads/product_comparison.html"

turns:
  - objective: "Ask to see recent files"
    ground_truth: null
    success_criteria: "Agent lists files including product_comparison.html"

  - objective: "Ask to analyze the product comparison doc by name only"
    ground_truth:
      doc_id: product_comparison
      fact_ids: [price_difference]
    success_criteria: "Agent finds and reads the file, provides analysis"

  - objective: "Ask a follow-up using a pronoun"
    ground_truth:
      doc_id: product_comparison
      fact_ids: [price_difference]
    success_criteria: "Agent answers from context without re-reading"

expected_outcome: |
  Agent recalls file paths from previous turns and answers
  follow-ups from conversation context.
```

### 6.2 Scenario Library (23 Scenarios)

#### Context Retention (4 scenarios)

| ID | Name | Severity |
|----|------|----------|
| `cross_turn_file_recall` | Cross-Turn File Recall | Critical |
| `pronoun_resolution` | Pronoun Resolution ("it", "that file") | Critical |
| `multi_doc_context` | Multi-Document Context (don't confuse A and B) | High |
| `conversation_summary` | 5+ Turn Summary | Medium |

#### RAG Quality (6 scenarios)

| ID | Name | Severity |
|----|------|----------|
| `simple_factual_rag` | Simple Factual RAG (direct lookup) | Critical |
| `cross_section_rag` | Cross-Section Synthesis | High |
| `table_extraction` | Table Data Extraction | High |
| `hallucination_resistance` | Admits when info NOT in doc | Critical |
| `negation_handling` | "Who is NOT eligible?" | High |
| `csv_analysis` | CSV Aggregation and Analysis | High |

#### Tool Selection (4 scenarios)

| ID | Name | Severity |
|----|------|----------|
| `smart_discovery` | Search → Index → Query (no pre-indexed docs) | Critical |
| `known_path_read` | Use read_file when path is known | High |
| `no_tools_needed` | Greetings, general knowledge | High |
| `multi_step_plan` | Complex multi-tool request | Medium |

#### Error Recovery (3 scenarios)

| ID | Name | Severity |
|----|------|----------|
| `search_empty_fallback` | Search returns empty → try alternatives | High |
| `file_not_found` | File doesn't exist → helpful error | Medium |
| `vague_request_clarification` | "Summarize the doc" with multiple docs | Medium |

#### Adversarial (3 scenarios)

| ID | Name | Severity |
|----|------|----------|
| `empty_file` | Empty file handling | Medium |
| `large_document` | Fact on page 52 of 75-page PDF | High |
| `topic_switch` | Rapid topic change mid-conversation | Medium |

#### Personality (3 scenarios)

| ID | Name | Severity |
|----|------|----------|
| `no_sycophancy` | Pushes back on wrong claims | Medium |
| `concise_response` | Short greeting → short reply | Medium |
| `honest_limitation` | Admits what it can't do | Medium |

---

## 7. Eval Webapp (Dashboard + Control Panel)

The eval webapp (`src/gaia/eval/webapp/`) is rewritten from the old read-only experiment
viewer into an **active control panel** for managing eval runs.

### 7.1 Dashboard (Read)

- **Summary view**: pass rate by category, score heatmap across scenarios, cost per run
- **Scenario detail**: per-turn conversation with user messages, agent responses, tool calls,
  judge scores, judge reasoning — full trace visualization
- **Comparison view**: side-by-side two runs to spot regressions (before/after a fix)
- **Trend view**: pass rate over time across multiple runs (daily/weekly)
- **Filter/sort**: by category, severity, status (PASS/FAIL/BLOCKED), score range

### 7.2 Control Panel (Write)

- **Trigger eval run**: start `gaia eval agent` from the UI (all scenarios or filtered)
- **Trigger fix run**: start `gaia eval agent --fix` from the UI
- **Monitor progress**: real-time status of running eval (current scenario, completed count)
- **Save baseline**: mark a run as the new baseline for regression comparison
- **Cancel run**: kill a running eval subprocess

### 7.3 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/agent-eval/runs` | GET | List all eval runs |
| `/api/agent-eval/runs/:runId` | GET | Load scorecard + traces for a run |
| `/api/agent-eval/runs/:runId/scenario/:id` | GET | Load single scenario trace |
| `/api/agent-eval/runs/:runId/compare/:baselineId` | GET | Diff two runs |
| `/api/agent-eval/start` | POST | Start `gaia eval agent` subprocess |
| `/api/agent-eval/start-fix` | POST | Start `gaia eval agent --fix` subprocess |
| `/api/agent-eval/status` | GET | Current run status (running/idle, progress) |
| `/api/agent-eval/stop` | POST | Kill running eval subprocess |
| `/api/agent-eval/baseline` | POST | Save a run as baseline |

### 7.4 Tech Stack

Rewritten from scratch but same tech: Express.js backend, vanilla JS frontend.
No new framework dependencies. Reads/writes to `eval/results/` directory.

---

## 8. Error Handling and Resilience

### 8.1 Sequential Execution Constraint

The Agent UI backend has a global chat semaphore set to `1` (`server.py`,
`asyncio.Semaphore(1)`). Only ONE `send_message` can execute at a time across
ALL sessions. This exists because `_TOOL_REGISTRY` is a module-level global.

**Eval scenarios run sequentially** — one `claude -p` subprocess at a time.
The Python runner manages the loop.

### 8.2 Failure Handling

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Lemonade server down | Pre-flight `system_status()` or `claude -p` returns MCP error | Abort run, report `INFRA_ERROR` |
| Agent UI timeout | `claude -p` subprocess exceeds timeout (default 300s) | Kill subprocess, log `TIMEOUT`, continue to next scenario |
| Claude Code subprocess crash | Non-zero exit code | Log `EVAL_ERROR`, continue to next scenario |
| Scenario file missing | Python file read fails | Log `CONFIG_ERROR`, skip, continue |
| Corpus document missing | Claude Code reports `index_document` failure | Log `SETUP_ERROR`, skip scenario |
| Partial eval run | Some scenarios complete, some error | Scorecard marks errored scenarios, reports what completed. Crash recovery resumes from last completed. |

### 8.3 Pre-flight Check

Python runner verifies prerequisites before running any scenarios:

```python
# Pre-flight (runs before any claude -p subprocess)
1. Check Agent UI health: GET http://localhost:4200/api/health
2. Check corpus files exist on disk
3. Check scenario files parseable
4. Check `claude` CLI is on PATH
5. Check eval/mcp-config.json exists
```

### 8.4 Crash Recovery

Following the `BatchExperimentRunner` pattern from existing eval framework:
- Each scenario result written to `eval/results/{run_id}/traces/{scenario_id}.json`
- Progress tracked in `eval/results/{run_id}/.progress.json`
- On resume, skip scenarios that already have result files
- `gaia eval agent --resume {run_id}` to continue an interrupted run

---

## 9. Cost Tracking

Each `claude -p` subprocess is capped via `--max-budget-usd 0.50`. The Python runner
tracks wall-clock time per scenario and accumulates cost estimates from the JSON results.

### 9.1 Cost Data Format

Each scenario's JSON result includes a `cost_estimate` field. The Python runner
aggregates these into the scorecard:

```json
{
  "cost": {
    "total_usd": 2.62,
    "total_eval_duration_minutes": 18.5,
    "model": "claude-sonnet-4-6",
    "budget_per_scenario_usd": 0.50,
    "by_scenario": {
      "cross_turn_file_recall": {"turns": 3, "cost_usd": 0.12, "duration_s": 45},
      "simple_factual_rag": {"turns": 2, "cost_usd": 0.08, "duration_s": 30}
    }
  }
}
```

### 9.2 Cost Optimization

| Technique | Savings |
|-----------|---------|
| `--max-budget-usd 0.50` per scenario | Hard cap prevents runaway costs |
| Run architecture audit first (free, no LLM) to skip blocked scenarios | Avoid wasted eval on impossible tests |
| Run single scenario during iteration (`--scenario X`) | Test one fix at a time |

### 9.3 Estimated Cost Per Full Run

| Component | Scenarios | Turns | Est. Cost |
|-----------|-----------|-------|-----------|
| Simulator + Judge (combined) | 23 | ~69 | ~$2.62 |
| Fix mode (per iteration) | failed only | varies | ~$0.50-1.00 |
| **Full eval run** | **23** | **~69** | **~$3** |

At ~$3 per full run, cheap enough to run multiple times per day.
Single-scenario run: ~$0.10-0.15.

---

## 10. Scorecard Format

### 10.1 JSON (for Claude Code consumption)

```json
{
  "run_id": "eval-2026-03-17-001",
  "timestamp": "2026-03-17T10:30:00Z",
  "config": {
    "backend_url": "http://localhost:4200",
    "local_model": "Qwen3.5-35B-A3B-GGUF",
    "eval_model": "claude-sonnet-4-6",
    "runner": "gaia eval agent (claude -p subprocess)",
    "system_prompt_hash": "sha256:a1b2c3...",
    "agent_file_hash": "sha256:d4e5f6...",
    "helpers_file_hash": "sha256:789abc..."
  },

  "architecture_audit": {
    "history_pairs": 2,
    "max_msg_chars": 500,
    "tool_results_in_history": false,
    "blocked_scenarios": ["cross_turn_file_recall"],
    "recommendations": [...]
  },

  "summary": {
    "total_scenarios": 23,
    "passed": 16,
    "failed": 4,
    "blocked": 2,
    "pass_rate": 0.80,
    "avg_score": 7.2,
    "by_category": {
      "context_retention": {"passed": 2, "failed": 1, "blocked": 1, "avg_score": 6.1},
      "rag_quality": {"passed": 5, "failed": 1, "blocked": 0, "avg_score": 7.8},
      "tool_selection": {"passed": 3, "failed": 1, "blocked": 0, "avg_score": 7.0},
      "error_recovery": {"passed": 2, "failed": 1, "blocked": 0, "avg_score": 6.5},
      "adversarial": {"passed": 3, "failed": 0, "blocked": 1, "avg_score": 7.5},
      "personality": {"passed": 3, "failed": 0, "blocked": 0, "avg_score": 8.2}
    }
  },

  "scenarios": [
    {
      "id": "cross_turn_file_recall",
      "status": "BLOCKED_BY_ARCHITECTURE",
      "blocked_by": "tool_results_in_history=false",
      "fix": {
        "target": "architecture",
        "file": "src/gaia/ui/_chat_helpers.py",
        "description": "Include tool result summaries in conversation history"
      }
    },
    {
      "id": "simple_factual_rag",
      "status": "PASS",
      "overall_score": 8.5,
      "turns": [
        {
          "turn": 1,
          "user_message": "What was Acme's Q3 revenue?",
          "agent_response": "According to the quarterly report, Acme Corp's Q3 2025 revenue was **$14.2 million**...",
          "agent_tools": ["query_documents"],
          "scores": {
            "correctness": 9, "tool_selection": 9, "context_retention": 10,
            "completeness": 8, "efficiency": 9, "personality": 7, "error_recovery": 10
          },
          "pass": true,
          "reasoning": "Correct answer matching ground truth. Used query_documents appropriately."
        }
      ]
    },
    {
      "id": "smart_discovery",
      "status": "FAIL",
      "overall_score": 3.0,
      "turns": [
        {
          "turn": 1,
          "user_message": "What's the PTO policy?",
          "agent_response": "I couldn't find any relevant documents...",
          "agent_tools": ["list_indexed_documents", "search_file"],
          "scores": {
            "correctness": 0, "tool_selection": 4, "context_retention": 5,
            "completeness": 0, "efficiency": 3, "personality": 5, "error_recovery": 2
          },
          "pass": false,
          "failure_category": "gave_up",
          "reasoning": "Agent searched for 'PTO policy' as filename — no file matches. Should have tried broader terms like 'employee handbook' or 'hr policy'."
        }
      ],
      "root_cause": "Smart Discovery workflow uses query keywords as file search patterns. Needs to extract likely document names, not just topic keywords.",
      "recommended_fix": {
        "target": "system_prompt",
        "file": "src/gaia/agents/chat/agent.py",
        "description": "In Smart Discovery section, instruct agent to search for common document names related to the topic, not just the exact query terms."
      }
    }
  ],

  "cost": {
    "estimated_total_usd": 2.62,
    "simulator_usd": 0.68,
    "judge_usd": 1.94,
    "by_scenario": {...}
  }
}
```

### 10.2 Markdown (terminal/human readable)

```markdown
# GAIA Agent Eval — 2026-03-17
**Model:** Qwen3-Coder-30B | **Eval:** claude-sonnet-4-6 | **Cost:** ~$2.62

## Architecture Audit
| Check | Value | Status |
|-------|-------|--------|
| History pairs | 2 | ⚠️ |
| Truncation | 500 chars | ⚠️ |
| Tool results in history | No | ❌ Critical |

## Results: 18/23 passed (78%) — 2 blocked
| Category | Pass | Fail | Blocked | Score |
|----------|------|------|---------|-------|
| Context Retention | 2 | 1 | 1 | 6.1 |
| RAG Quality | 5 | 1 | 0 | 7.8 |
| Tool Selection | 3 | 1 | 0 | 7.0 |
| Error Recovery | 2 | 1 | 0 | 6.5 |
| Adversarial | 3 | 0 | 1 | 7.5 |
| Personality | 3 | 0 | 0 | 8.2 |

## Top Fixes
1. [Critical] Tool results not in history → `_chat_helpers.py`
2. [High] Smart Discovery search terms too narrow → `agent.py`
3. [High] Hallucination on absent facts → `agent.py`
```

---

## 11. CLI Interface

The eval has two modes:

| Mode | Flag | What It Does |
|------|------|-------------|
| **Evaluate only** (default) | `gaia eval agent` | Run scenarios, judge responses, produce scorecard. No code changes. |
| **Evaluate + Fix** | `gaia eval agent --fix` | Run scenarios, judge, then invoke Claude Code to fix failures and re-eval. Iterates until pass rate target is met or max iterations reached. |

### 11.1 Evaluate Only (Default)

```
gaia eval agent
  │
  ├── For each scenario:
  │     claude -p "{scenario_prompt}" --mcp-config ... --json-schema ...
  │     → JSON result (scores, pass/fail, root cause)
  │
  ├── Aggregate into scorecard.json + summary.md
  └── Done. Human reviews scorecard, decides what to fix.
```

### 11.2 Evaluate + Fix (`--fix`)

```
gaia eval agent --fix
  │
  ├── Phase A: EVAL — run all scenarios, produce scorecard
  │
  ├── Phase B: FIX — invoke Claude Code to fix failures
  │     claude -p "{fixer_prompt}" --permission-mode auto
  │     → Claude Code reads scorecard, reads source files,
  │       makes targeted fixes (system prompt, architecture, tool descriptions)
  │     → Does NOT commit changes
  │
  ├── Phase C: RE-EVAL — re-run ONLY the previously failed scenarios
  │     → Produce updated scorecard
  │
  ├── Phase D: COMPARE — diff before/after scorecards
  │     → Report: which failures were fixed, any regressions
  │
  └── Repeat B→C→D up to --max-fix-iterations (default: 3)
      or until --target-pass-rate reached (default: 0.90)
```

**Fix mode safeguards:**
- Claude Code runs with `--permission-mode auto` but does NOT commit
- All changes are left unstaged for human review
- A `fix_log.json` records every change made, which scenario it targeted, and whether it helped
- If a fix causes a regression (previously passing scenario now fails), the fix is flagged
- `--max-fix-iterations 3` prevents infinite loops
- `--target-pass-rate 0.90` stops early if target met
- Architecture issues (`BLOCKED_BY_ARCHITECTURE`) are fixed first, then prompt issues

**Fixer prompt** (invoked as `claude -p`):

```
You are the GAIA Agent Fixer. Read the eval scorecard and fix the agent.

## INPUT
- Scorecard: eval/results/{run_id}/scorecard.json
- Summary: eval/results/{run_id}/summary.md

## RULES
1. Fix ARCHITECTURE issues first (in _chat_helpers.py, agent.py base classes)
   — these unblock BLOCKED_BY_ARCHITECTURE scenarios
2. Then fix PROMPT issues (in agent.py system prompt, tool descriptions)
   — these fix FAILED scenarios
3. Make minimal, targeted changes — do NOT rewrite entire files
4. Do NOT commit changes — leave for human review
5. Write a fix log to eval/results/{run_id}/fix_log.json:
   [{"file": "...", "change": "...", "targets_scenario": "...", "rationale": "..."}]

## PRIORITY ORDER
Fix failures in this order:
1. Critical severity first
2. Architecture fixes before prompt fixes
3. Failures that affect multiple scenarios before single-scenario fixes
```

### 11.3 CLI Commands

```bash
# ── Evaluate Only ──────────────────────────────────
gaia eval agent                                    # Full eval (23 scenarios)
gaia eval agent --category context_retention       # Single category
gaia eval agent --scenario cross_turn_file_recall  # Single scenario
gaia eval agent --audit-only                       # Architecture audit (free, instant)

# ── Evaluate + Fix ─────────────────────────────────
gaia eval agent --fix                              # Eval → fix → re-eval (up to 3 iterations)
gaia eval agent --fix --max-fix-iterations 5       # More iterations
gaia eval agent --fix --target-pass-rate 0.95      # Higher bar
gaia eval agent --fix --category rag_quality       # Fix only one category

# ── Corpus & Utilities ─────────────────────────────
gaia eval agent --generate-corpus                  # Regenerate synthetic docs
gaia eval agent --compare baseline.json current.json  # Regression detection
gaia eval agent --save-baseline                    # Save current as baseline
gaia eval agent --capture-session <id>             # Convert real conversation to scenario

# ── Configuration ──────────────────────────────────
gaia eval agent --backend http://localhost:4200
gaia eval agent --eval-model claude-sonnet-4-6
gaia eval agent --output eval/results/
gaia eval agent --resume {run_id}                  # Resume interrupted run
```

---

## 12. File Structure

The entire `src/gaia/eval/` directory is replaced. Old files are removed.

```
src/gaia/eval/
├── __init__.py
├── runner.py                # AgentEvalRunner — main orchestrator, claude -p subprocess loop
├── audit.py                 # Deterministic architecture audit (no LLM)
├── scorecard.py             # JSON + Markdown scorecard generation + comparison
├── claude.py                # (kept) ClaudeClient — Anthropic SDK wrapper, cost tracking
├── config.py                # (kept) Model pricing, DEFAULT_CLAUDE_MODEL
├── pdf_generator.py         # (kept, renamed) PDF corpus document generator
├── webapp/
│   ├── server.js            # Rewritten — agent eval API endpoints
│   └── public/
│       └── app.js           # Rewritten — scenario detail, score heatmap, comparison view

eval/
├── corpus/
│   ├── generator/
│   │   ├── __init__.py
│   │   ├── generate_all.py
│   │   ├── gen_pdf.py
│   │   ├── gen_csv.py
│   │   ├── gen_markdown.py
│   │   ├── gen_html.py
│   │   ├── gen_code.py
│   │   ├── gen_text.py
│   │   └── gen_adversarial.py
│   ├── manifest.json
│   ├── documents/           # Generated (gitignored)
│   └── adversarial/         # Generated (gitignored)
├── scenarios/
│   ├── context_retention/
│   │   ├── cross_turn_file_recall.yaml
│   │   ├── pronoun_resolution.yaml
│   │   ├── multi_doc_context.yaml
│   │   └── conversation_summary.yaml
│   ├── rag_quality/
│   │   ├── simple_factual_rag.yaml
│   │   ├── cross_section_rag.yaml
│   │   ├── table_extraction.yaml
│   │   ├── hallucination_resistance.yaml
│   │   ├── negation_handling.yaml
│   │   └── csv_analysis.yaml
│   ├── tool_selection/
│   │   ├── smart_discovery.yaml
│   │   ├── known_path_read.yaml
│   │   ├── no_tools_needed.yaml
│   │   └── multi_step_plan.yaml
│   ├── error_recovery/
│   │   ├── search_empty_fallback.yaml
│   │   ├── file_not_found.yaml
│   │   └── vague_request_clarification.yaml
│   ├── adversarial/
│   │   ├── empty_file.yaml
│   │   ├── large_document.yaml
│   │   └── topic_switch.yaml
│   └── personality/
│       ├── no_sycophancy.yaml
│       ├── concise_response.yaml
│       └── honest_limitation.yaml
├── baselines/               # Saved baseline scorecards
├── results/                 # Eval run outputs
│   └── {run_id}/
│       ├── scorecard.json
│       ├── summary.md
│       └── traces/          # Per-scenario conversation traces
└── prompts/
    ├── simulator.md         # User simulator system prompt
    ├── judge_turn.md        # Per-turn judge prompt
    ├── judge_scenario.md    # Scenario-level judge prompt
    └── fixer.md             # Agent fixer prompt

# CLI integration
src/gaia/cli.py              # Add `gaia eval agent` subcommand (modify)
```

---

## 13. Implementation Phases

### Phase 0: Proof of Concept (Day 1)

**Goal:** Validate the entire eval loop end-to-end with ONE scenario, ONE document, ZERO
Python infrastructure. Just `claude -p` + Agent UI MCP.

**Steps:**
1. Hand-write ONE corpus document (`eval/corpus/documents/product_comparison.html`)
   with 3 known facts
2. Create `eval/mcp-config.json` with Agent UI MCP server config (see §1.4)
3. Ensure Agent UI backend is running on :4200 with Lemonade + model loaded
4. Run `claude -p` with this **ready-to-paste** prompt:

```
You are testing the GAIA Agent UI. Use the gaia-agent-ui MCP tools to drive a conversation
and evaluate the agent's responses.

GROUND TRUTH: The file eval/corpus/documents/product_comparison.html contains a comparison
of two software products. Known facts:
- Product A costs $49/month, Product B costs $79/month (a $30/month difference)
- Product A has 10 integrations, Product B has 25 integrations
- Product A is rated 4.2 stars, Product B is rated 4.7 stars

STEPS:
1. Call system_status() to verify GAIA is running
2. Call create_session("Eval: Phase 0 Test")
3. Call index_document with the ABSOLUTE path to eval/corpus/documents/product_comparison.html
4. Call send_message with: "What products are being compared and how do their prices differ?"
5. Evaluate: Did the agent mention $49, $79, and the $30 difference? Score correctness 0-10.
6. Call send_message with: "Which one has more integrations?"
7. Evaluate: Did the agent correctly say Product B with 25? Score 0-10.
8. Call send_message with: "What about ratings?"
9. Evaluate: Did the agent get 4.2 and 4.7? Score 0-10.
10. Call get_messages to get the full conversation with agent steps
11. Write a results JSON file to eval/results/phase0/result.json with:
    - Each turn's user message, agent response, tools used, score, pass/fail
    - Overall pass rate and average score
    - Any failures with root cause analysis
12. Call delete_session to clean up
13. Print a summary of what passed and what failed
```

**Success if:** The task creates a session, sends 3 messages via MCP, captures traces,
writes a result JSON, and gives honest scores. Even rough scores are fine — the loop works.

**No Python code. No generators. No CLI command. Just prompt + MCP + one document.**

This validates the architecture before investing in infrastructure.

### Phase 1: Corpus Generation + Architecture Audit (Week 1)

**Deliverables:**
- Synthetic document generators (`eval/corpus/generator/*.py`)
  - Keep `PDFDocumentGenerator` (8 templates, ReportLab) for PDF corpus docs
  - New generators for CSV, HTML, Python, TXT formats
  - Use `claude -p` to auto-extract Q&A facts from generated documents into manifest
- 18 documents with `manifest.json` (100+ verifiable facts)
- Architecture audit (`src/gaia/eval/audit.py`)
- `gaia eval agent --audit-only` and `gaia eval agent --generate-corpus`

**Cost:** Ground truth generation uses Claude API via existing `ClaudeClient`.
Estimated ~$1-2 for one-time corpus generation (18 docs × ~5 facts each).

### Phase 2: Eval Agent Prompts + 5 Scenarios + Scorecard (Week 2-3)

**Deliverables:**
- Simulator prompt (`eval/prompts/simulator.md`)
- Judge prompts (`eval/prompts/judge_turn.md`, `judge_scenario.md`)
- 5 critical scenarios (YAML files)
- Eval runner with scenario loading (`src/gaia/eval/runner.py`)
- Scorecard generator (`src/gaia/eval/scorecard.py`)
- CLI integration (`src/gaia/cli.py` — replace old `gaia eval` with `gaia eval agent`)
- MCP config for Claude Code subprocess (`eval/mcp-config.json`)

**Absorbs from old eval framework:**
- `ClaudeClient` (kept as-is) for any direct API calls needed
- `calculate_similarity()` logic absorbed into `scorecard.py`
- Crash recovery pattern (`.progress.json`, resume-on-failure) absorbed into `runner.py`
- `config.py` MODEL_PRICING kept for cost calculation

**End of Phase 2:** `gaia eval agent` works end-to-end. Old eval framework removed.

### Phase 3: Fix Mode + Full Scenario Library (Week 4)

**Deliverables:**
- `--fix` mode: eval → fix → re-eval loop with Claude Code fixer subprocess
- Fix log tracking (`fix_log.json`), regression detection, iteration limits
- Fixer prompt (`eval/prompts/fixer.md`)
- Remaining 17 scenarios (full 23-scenario library)
- `--compare` for regression detection between runs
- `--save-baseline` for baselines
- `--capture-session` for converting real conversations to scenarios
- Eval webapp extension: Agent Eval tab with scenario detail + comparison view

### Phase 4: Iterate (Ongoing)

Not pre-planned. Driven by:
- Real user failures converted to scenarios via `--capture-session`
- Judge-recommended new test cases from scorecard `recommended_fix` fields
- Regression patterns observed across `--compare` runs

---

## 14. Prerequisites

| Requirement | How to Verify |
|-------------|---------------|
| Lemonade server running with model | `gaia llm "hello"` |
| Agent UI backend running | `curl http://localhost:4200/api/health` |
| `ANTHROPIC_API_KEY` set | `.env` file or environment variable |
| Eval deps installed | `uv pip install -e ".[eval]"` |
| Corpus generated | `gaia eval agent --generate-corpus` |
| `eval/mcp-config.json` exists | Check file contains gaia-agent-ui server config |
| `claude` CLI on PATH | `claude --version` |

---

## 15. Success Criteria

| Criterion | Target |
|-----------|--------|
| `gaia eval agent` produces actionable scorecard | ✅ |
| `gaia eval agent --fix` runs eval→fix→re-eval loop autonomously | ✅ |
| Scorecard includes per-turn Claude judge scores (0-10) | ✅ |
| Architecture audit identifies blocked vs failed scenarios | ✅ |
| Fix mode prioritizes architecture fixes before prompt fixes | ✅ |
| Fix mode tracks all changes in `fix_log.json` with rationale | ✅ |
| Fix mode detects regressions (fix broke a passing scenario) | ✅ |
| Fix mode respects `--max-fix-iterations` and `--target-pass-rate` | ✅ |
| Catches the file recall bug from real user session | ✅ |
| 23 scenarios across 6 categories | ✅ |
| Synthetic corpus with 100+ verifiable facts | ✅ |
| `--compare` detects regressions between runs | ✅ |
| Pre-flight check catches infra failures before spending money | ✅ |
| Full eval run completes in &lt;60 min on NPU, &lt;3 hrs on CPU | ✅ |
| Full eval run costs &lt;$5 in cloud LLM usage | ✅ |

---

## 16. Known Constraints and Trade-offs

| Constraint | Source | Mitigation |
|-----------|--------|------------|
| Agent UI chat semaphore = 1 | `server.py` `Semaphore(1)` — global `_TOOL_REGISTRY` | Scenarios run sequentially via Python loop. No parallel `send_message`. |
| Non-deterministic responses | Local LLM (Qwen3-30B) varies per run | Judge accounts for this; trends over multiple runs matter more than single run |
| MCP `send_message` truncates traces | Thinking: 150 chars, tool_args: 200, results: 300 | Phase 4 calls `get_messages()` for fuller data. Judge works with same visibility as any MCP client. |
| MCP `get_messages` also truncates | Content: 2000 chars, step results: 300 chars | Eval agent can use `browse_files`/`preview_file` or read from disk for full text |
| `claude -p` subprocess cost | Each scenario invokes a Claude Code session | `--max-budget-usd 0.50` caps per scenario; ~$3 total for 23 scenarios |
| `--json-schema` complexity | Large nested schemas may be imprecise | Schema covers top-level structure; nested turns validated by prompt instructions |
| Eval agent judges its own simulation | Same Claude Code session simulates user + judges | Pragmatic trade-off; if bias detected, split into two `claude -p` calls per scenario |
