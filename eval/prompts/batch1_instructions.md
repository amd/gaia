# Eval Batch 1 — 5 Scenarios

Read this file completely before starting. Execute all 5 scenarios in order.

## CRITICAL RULES (NEVER VIOLATE)
- NEVER call `delete_session` on ANY session
- ALWAYS pass `session_id` when calling `index_document`
- Results: `eval/results/rerun/<scenario_id>.json`
- Log progress to: `eval/eval_run_report.md` (append only)
- Corpus path: `C:/Users/14255/Work/gaia4/eval/corpus/documents/`

## SCORING FORMULA
overall_score = correctness×0.25 + tool_selection×0.20 + context_retention×0.20 + completeness×0.15 + efficiency×0.10 + personality×0.05 + error_recovery×0.05
PASS = overall_score ≥ 6.0

## FIX PROTOCOL — APPLY AFTER EACH TURN
After each agent response, evaluate it against the ground truth. If a turn would score below 6.0 OR shows a known failure pattern:
1. **Path resolution failure** (agent uses wrong path like C:/Users/14255/employee_handbook.md): Re-send the same question. Fix 1 (basename fallback) should handle it. If still failing after 2 retries, document and move on.
2. **No answer / incomplete response** (agent stops mid-way): Re-send: "Please complete your answer."
3. **Verbose response to short question**: Re-send: "Please give a shorter answer — 1-2 sentences max."
4. **Wrong document used** (cross-document contamination): Re-send with explicit context: "Please only use [filename] for this answer."
5. **Hallucinated fact**: Re-send: "Please check the document — I believe that figure may be incorrect."
After applying a fix, re-score that turn with the corrected response and note "fix_applied: true" in the JSON.
Maximum 2 fix attempts per turn. If still failing after 2 attempts, score honestly and document root cause.

---

## Scenario 1: simple_factual_rag

**Setup:**
1. `create_session` name="Rerun: simple_factual_rag" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md" session_id=<session_id>

**Turns:**
- Turn 1: "What was Acme Corp's Q3 2025 revenue?"
  - Ground truth: "$14.2 million" (23% YoY from $11.5M)
  - CRITICAL FAIL if wrong dollar amount
- Turn 2: "What was the year-over-year revenue growth?"
  - Ground truth: "23%" and "$11.5 million" baseline
  - CRITICAL FAIL if wrong %
- Turn 3: "What's the CEO's outlook for Q4?"
  - Ground truth: "15-18% growth driven by enterprise segment expansion and three new product launches planned for November"

**Previous score:** 9.42 — PASS

---

## Scenario 2: hallucination_resistance

**Setup:**
1. `create_session` name="Rerun: hallucination_resistance" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md" session_id=<session_id>
3. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md" session_id=<session_id>
4. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/product_comparison.html" session_id=<session_id>

**Turns:**
- Turn 1: "What was Acme Corp's Q3 2025 revenue?"
  - Ground truth: "$14.2 million"
  - CRITICAL FAIL if wrong
- Turn 2: "How many employees does Acme Corp have?"
  - Ground truth: NOT in the documents — agent must say "information not available" without fabricating a number
  - CRITICAL FAIL if agent invents an employee count

**Previous score:** 9.625 — PASS

---

## Scenario 3: pronoun_resolution

**Setup:**
1. `create_session` name="Rerun: pronoun_resolution" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md" session_id=<session_id>

**Turns:**
- Turn 1: "What is the PTO policy for new employees?"
  - Ground truth: 15 days for first-year employees, accruing at 1.25 days/month, full-time only
- Turn 2: "What about remote work — does it have a policy too?"
  - Ground truth: Up to 3 days/week with manager approval; fully remote needs VP-level approval
  - KEY TEST: agent must resolve "it" as referring to the employee handbook/company policies without asking for clarification
- Turn 3: "Does that policy apply to contractors too?"
  - Ground truth: No — contractors are NOT eligible per Sections 3 and 5; benefits for full-time employees only

**Previous score:** 8.73 — PASS

---

## Scenario 4: cross_turn_file_recall

**Setup:**
1. `create_session` name="Rerun: cross_turn_file_recall" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md" session_id=<session_id>
3. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md" session_id=<session_id>
4. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/product_comparison.html" session_id=<session_id>

**Turns:**
- Turn 1: "What documents do you have access to?"
  - Ground truth: Agent lists indexed docs (acme_q3_report.md, employee_handbook.md, product_comparison.html)
  - PASS if no tool calls needed (can answer from system context)
- Turn 2: "How much do the two products cost?"
  - Ground truth: StreamLine $49/month, ProFlow $79/month
  - KEY TEST: agent must resolve "the two products" from Turn 1 context (product_comparison.html) without asking which document
  - CRITICAL FAIL if agent asks "which document?" or "which products?"
- Turn 3: "Which one is better value for money?"
  - Ground truth: ProFlow — 25 integrations vs 10, 4.7 rating vs 4.2, $30/month premium; ProFlow recommended for feature-rich needs, StreamLine for budget-conscious

**Previous score:** 9.42 — PASS

---

## Scenario 5: smart_discovery

**NOTE:** This scenario tests the agent's ability to DISCOVER and index a document it doesn't already have. Do NOT pre-index the document. The agent should use browse_files/search_files to find it.
**IMPORTANT:** With Fix 3, when the agent calls index_document without session_id, the doc goes to library-only mode and the agent may not see it in session context. This is an architectural limitation — score tool_selection/error_recovery based on whether the agent ATTEMPTS the right discovery path, even if RAG results are incomplete.

**Setup:**
1. `create_session` name="Rerun: smart_discovery" → note session_id
2. Do NOT index any documents

**Turns:**
- Turn 1: "What is the PTO policy for new employees?"
  - Ground truth: 15 days for first-year employees
  - KEY TEST: Does agent use browse_files/search_files/index_document to discover employee_handbook.md?
  - NOTE: If agent finds doc via global library index residue, that's acceptable but penalize tool_selection
- Turn 2: "What about the remote work policy?"
  - Ground truth: Up to 3 days/week with manager approval; fully remote needs VP-level approval
  - Test: Can agent recall the document found in Turn 1?

**Previous score:** 8.97 — PASS (but discovery was bypassed)

---

## After All 5 Scenarios:

For each scenario, write JSON to `eval/results/rerun/<scenario_id>.json`:
```json
{
  "scenario_id": "...",
  "run": "rerun",
  "previous_score": X.XX,
  "status": "PASS or FAIL",
  "overall_score": X.XX,
  "turns": [
    {
      "turn": N,
      "user_message": "...",
      "agent_response_summary": "...",
      "agent_tools": [...],
      "tool_steps": N,
      "scores": {
        "correctness": N, "tool_selection": N, "context_retention": N,
        "completeness": N, "efficiency": N, "personality": N, "error_recovery": N
      },
      "overall_score": X.XX,
      "pass": true/false,
      "notes": "..."
    }
  ],
  "improvement": "improved/no_change/regressed",
  "notes": "..."
}
```

Append to `eval/eval_run_report.md`:
```
### [TIMESTAMP] Batch 1 Results
| Scenario | Prev | New | Delta | Status |
...
```

Print "BATCH 1 COMPLETE" when all 5 done.
