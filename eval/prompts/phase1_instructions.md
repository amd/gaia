# Phase 1 Instructions — Corpus Generation + Architecture Audit

## GOAL
Build the full eval corpus (18 documents with known facts) and the architecture audit module.
Write everything to disk. Do NOT run any eval scenarios yet.

## PART A: Update/Create Corpus Documents

### A1. Verify existing documents match required facts

Check `C:\Users\14255\Work\gaia4\eval\corpus\documents\` — currently has:
- acme_q3_report.md
- budget_2025.md
- employee_handbook.md
- product_comparison.html (already correct)

**Update `employee_handbook.md`** to embed these EXACT verifiable facts:
- First-year PTO: **15 days**
- Remote work: **Up to 3 days/week with manager approval. Fully remote requires VP approval.**
- Contractors: **NOT eligible for health benefits (full-time employees only)**
- Section structure: 12 sections numbered 1-12

**Update `acme_q3_report.md`** to embed these EXACT verifiable facts:
- Q3 2025 revenue: **$14.2 million**
- YoY growth: **23% increase from Q3 2024's $11.5 million**
- CEO Q4 outlook: **Projected 15-18% growth driven by enterprise segment expansion**
- Employee count: **NOT mentioned anywhere** (for hallucination resistance testing)

### A2. Create new corpus documents

**Create `C:\Users\14255\Work\gaia4\eval\corpus\documents\sales_data_2025.csv`**
500 rows of sales data with columns: date,product,units,unit_price,revenue,region,salesperson
Rules:
- Best-selling product in March 2025: **Widget Pro X, 142 units, $28,400 revenue** (unit_price=$200)
- Q1 2025 total revenue: **$342,150**
- Top salesperson: **Sarah Chen, $67,200**
- Use random seed 42 for all other data
- Date range: 2025-01-01 to 2025-03-31
- Products: Widget Pro X, Widget Basic, Gadget Plus, Gadget Lite, Service Pack
- Regions: North, South, East, West
- Salespeople: Sarah Chen, John Smith, Maria Garcia, David Kim, Emily Brown

**Create `C:\Users\14255\Work\gaia4\eval\corpus\documents\api_reference.py`**
A Python file with docstrings documenting a fictional REST API.
Must embed: **Authentication uses Bearer token via the Authorization header**
Include: 3-4 endpoint functions with full docstrings, type hints, example usage

**Create `C:\Users\14255\Work\gaia4\eval\corpus\documents\meeting_notes_q3.txt`**
Plain text meeting notes. Must embed: **Next meeting: October 15, 2025 at 2:00 PM**
Include: attendees, agenda items, decisions, action items

**Create `C:\Users\14255\Work\gaia4\eval\corpus\documents\large_report.md`**
A long markdown document (~75 "pages" worth of content, ~15,000 words).
Must embed in Section 52 equivalent: **"Three minor non-conformities in supply chain documentation"**
(This tests deep retrieval — the fact must be buried deep in the document)
Use realistic-looking audit/compliance report content.

**Create adversarial documents:**
- `C:\Users\14255\Work\gaia4\eval\corpus\adversarial\empty.txt` — empty file (0 bytes)
- `C:\Users\14255\Work\gaia4\eval\corpus\adversarial\unicode_test.txt` — text with heavy Unicode: Chinese, Arabic, emoji, mathematical symbols, mixed scripts
- `C:\Users\14255\Work\gaia4\eval\corpus\adversarial\duplicate_sections.md` — markdown with 5 identical sections repeated 3 times each (tests deduplication)

Create the `C:\Users\14255\Work\gaia4\eval\corpus\adversarial\` directory if it doesn't exist.

## PART B: Create corpus manifest.json

Write `C:\Users\14255\Work\gaia4\eval\corpus\manifest.json`:
```json
{
  "generated_at": "2026-03-20T00:00:00Z",
  "total_documents": 9,
  "total_facts": 15,
  "documents": [
    {
      "id": "product_comparison",
      "filename": "product_comparison.html",
      "format": "html",
      "domain": "product",
      "facts": [
        {"id": "price_a", "question": "How much does StreamLine cost per month?", "answer": "$49/month", "difficulty": "easy"},
        {"id": "price_b", "question": "How much does ProFlow cost per month?", "answer": "$79/month", "difficulty": "easy"},
        {"id": "price_diff", "question": "What is the price difference between the products?", "answer": "$30/month (ProFlow costs more)", "difficulty": "easy"},
        {"id": "integrations_a", "question": "How many integrations does StreamLine have?", "answer": "10", "difficulty": "easy"},
        {"id": "integrations_b", "question": "How many integrations does ProFlow have?", "answer": "25", "difficulty": "easy"},
        {"id": "rating_a", "question": "What is StreamLine's star rating?", "answer": "4.2 out of 5", "difficulty": "easy"},
        {"id": "rating_b", "question": "What is ProFlow's star rating?", "answer": "4.7 out of 5", "difficulty": "easy"}
      ]
    },
    {
      "id": "employee_handbook",
      "filename": "employee_handbook.md",
      "format": "markdown",
      "domain": "hr_policy",
      "facts": [
        {"id": "pto_days", "question": "How many PTO days do first-year employees get?", "answer": "15 days", "difficulty": "easy"},
        {"id": "remote_work", "question": "What is the remote work policy?", "answer": "Up to 3 days/week with manager approval. Fully remote requires VP approval.", "difficulty": "medium"},
        {"id": "contractor_benefits", "question": "Are contractors eligible for health benefits?", "answer": "No — benefits are for full-time employees only", "difficulty": "hard"}
      ]
    },
    {
      "id": "acme_q3_report",
      "filename": "acme_q3_report.md",
      "format": "markdown",
      "domain": "finance",
      "facts": [
        {"id": "q3_revenue", "question": "What was Acme Corp's Q3 2025 revenue?", "answer": "$14.2 million", "difficulty": "easy"},
        {"id": "yoy_growth", "question": "What was the year-over-year revenue growth?", "answer": "23% increase from Q3 2024's $11.5 million", "difficulty": "medium"},
        {"id": "ceo_outlook", "question": "What is the CEO's Q4 outlook?", "answer": "Projected 15-18% growth driven by enterprise segment expansion", "difficulty": "medium"},
        {"id": "employee_count", "question": "How many employees does Acme have?", "answer": null, "difficulty": "hard", "note": "NOT in document — agent must say it doesn't know"}
      ]
    },
    {
      "id": "sales_data",
      "filename": "sales_data_2025.csv",
      "format": "csv",
      "domain": "sales",
      "facts": [
        {"id": "top_product_march", "question": "What was the best-selling product in March 2025?", "answer": "Widget Pro X with 142 units and $28,400 revenue", "difficulty": "medium"},
        {"id": "q1_total_revenue", "question": "What was total Q1 2025 revenue?", "answer": "$342,150", "difficulty": "medium"},
        {"id": "top_salesperson", "question": "Who was the top salesperson by revenue?", "answer": "Sarah Chen with $67,200", "difficulty": "medium"}
      ]
    },
    {
      "id": "api_docs",
      "filename": "api_reference.py",
      "format": "python",
      "domain": "technical",
      "facts": [
        {"id": "auth_method", "question": "What authentication method does the API use?", "answer": "Bearer token via the Authorization header", "difficulty": "easy"}
      ]
    },
    {
      "id": "meeting_notes",
      "filename": "meeting_notes_q3.txt",
      "format": "text",
      "domain": "general",
      "facts": [
        {"id": "next_meeting", "question": "When is the next meeting?", "answer": "October 15, 2025 at 2:00 PM", "difficulty": "easy"}
      ]
    },
    {
      "id": "large_report",
      "filename": "large_report.md",
      "format": "markdown",
      "domain": "compliance",
      "facts": [
        {"id": "buried_fact", "question": "What was the compliance finding in Section 52?", "answer": "Three minor non-conformities in supply chain documentation", "difficulty": "hard"}
      ]
    }
  ],
  "adversarial_documents": [
    {"id": "empty_file", "filename": "empty.txt", "expected_behavior": "Agent reports file is empty"},
    {"id": "unicode_heavy", "filename": "unicode_test.txt", "expected_behavior": "No encoding errors"},
    {"id": "duplicate_content", "filename": "duplicate_sections.md", "expected_behavior": "Agent does not return duplicate chunks"}
  ]
}
```

## PART C: Architecture Audit

Write `C:\Users\14255\Work\gaia4\src\gaia\eval\audit.py`:

This module performs a deterministic (no LLM) inspection of the GAIA agent architecture to identify structural limitations before running scenarios.

```python
"""
Architecture audit for GAIA Agent Eval.
Deterministic checks — no LLM calls needed.
"""
import ast
import json
from pathlib import Path


GAIA_ROOT = Path(__file__).parent.parent.parent.parent  # src/gaia/eval/ -> repo root


def audit_chat_helpers() -> dict:
    """Read _chat_helpers.py and extract key constants."""
    path = GAIA_ROOT / "src" / "gaia" / "ui" / "_chat_helpers.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    constants = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("_MAX"):
                    if isinstance(node.value, ast.Constant):
                        constants[target.id] = node.value.value
    return constants


def audit_agent_persistence(chat_router_path: Path = None) -> str:
    """Check if ChatAgent is recreated per-request or persisted."""
    if chat_router_path is None:
        chat_router_path = GAIA_ROOT / "src" / "gaia" / "ui" / "routers" / "chat.py"
    source = chat_router_path.read_text(encoding="utf-8")
    # Check for agent creation inside the request handler vs module level
    if "ChatAgent(" in source:
        # Heuristic: if ChatAgent is created inside an async def, it's per-request
        return "stateless_per_message"
    return "unknown"


def audit_tool_results_in_history(chat_helpers_path: Path = None) -> bool:
    """Check if tool results are included in conversation history."""
    if chat_helpers_path is None:
        chat_helpers_path = GAIA_ROOT / "src" / "gaia" / "ui" / "_chat_helpers.py"
    source = chat_helpers_path.read_text(encoding="utf-8")
    # Look for agent_steps or tool results being added to history
    return "agent_steps" in source and "tool" in source.lower()


def run_audit() -> dict:
    """Run the full architecture audit and return results."""
    constants = audit_chat_helpers()
    history_pairs = constants.get("_MAX_HISTORY_PAIRS", "unknown")
    max_msg_chars = constants.get("_MAX_MSG_CHARS", "unknown")
    tool_results_in_history = audit_tool_results_in_history()
    agent_persistence = audit_agent_persistence()

    blocked_scenarios = []
    recommendations = []

    if history_pairs != "unknown" and int(history_pairs) < 5:
        recommendations.append({
            "id": "increase_history_pairs",
            "impact": "high",
            "file": "src/gaia/ui/_chat_helpers.py",
            "description": f"_MAX_HISTORY_PAIRS={history_pairs} limits multi-turn context. Increase to 10+."
        })

    if max_msg_chars != "unknown" and int(max_msg_chars) < 1000:
        recommendations.append({
            "id": "increase_truncation",
            "impact": "high",
            "file": "src/gaia/ui/_chat_helpers.py",
            "description": f"_MAX_MSG_CHARS={max_msg_chars} truncates messages. Increase to 2000+."
        })
        blocked_scenarios.append({
            "scenario": "cross_turn_file_recall",
            "blocked_by": f"max_msg_chars={max_msg_chars}",
            "explanation": "File paths from previous turns may be truncated in history."
        })

    if not tool_results_in_history:
        recommendations.append({
            "id": "include_tool_results",
            "impact": "critical",
            "file": "src/gaia/ui/_chat_helpers.py",
            "description": "Tool result summaries not detected in history. Cross-turn tool data unavailable."
        })
        blocked_scenarios.append({
            "scenario": "cross_turn_file_recall",
            "blocked_by": "tool_results_in_history=false",
            "explanation": "File paths from list_recent_files are in tool results, not passed to LLM next turn."
        })

    return {
        "architecture_audit": {
            "history_pairs": history_pairs,
            "max_msg_chars": max_msg_chars,
            "tool_results_in_history": tool_results_in_history,
            "agent_persistence": agent_persistence,
            "blocked_scenarios": blocked_scenarios,
            "recommendations": recommendations
        }
    }


if __name__ == "__main__":
    result = run_audit()
    print(json.dumps(result, indent=2))
```

## PART D: Run the Architecture Audit

After writing audit.py, run it:
```
uv run python C:\Users\14255\Work\gaia4\src\gaia\eval\audit.py
```

Write the output to `C:\Users\14255\Work\gaia4\eval\results\phase1\architecture_audit.json`

## PART E: Verify

After all steps, verify:
1. All corpus documents exist with correct content
2. manifest.json is valid JSON with all documents listed
3. audit.py runs without errors
4. architecture_audit.json was written

Write a completion report to `C:\Users\14255\Work\gaia4\eval\results\phase1\phase1_complete.md` summarizing what was created and any issues found.

## IMPORTANT
- Use absolute Windows paths (C:\...) for all file operations
- Do NOT delete any files
- The CSV must have exactly the right totals for testing (Sarah Chen=$67,200, Widget Pro X in March=142 units/$28,400, Q1 total=$342,150)
- For the large_report.md, the buried fact must appear after substantial content (simulate being on "page 52" by placing it ~75% through the document)
