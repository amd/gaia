# Eval Batch 2 — 4 Scenarios

Read this file completely before starting. Execute all 4 scenarios in order.

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
1. **Path resolution failure**: Re-send the same question. Fix 1 (basename fallback) should handle it. If still failing after 2 retries, document and move on.
2. **No answer / incomplete response**: Re-send: "Please complete your answer."
3. **Verbose response to short question**: Re-send: "Please give a shorter answer — 1-2 sentences max."
4. **Wrong document used**: Re-send with explicit context: "Please only use [filename] for this answer."
5. **Hallucinated fact**: Re-send: "Please check the document — I believe that figure may be incorrect."
After applying a fix, re-score that turn with the corrected response and note "fix_applied: true" in the JSON.
Maximum 2 fix attempts per turn. If still failing after 2 attempts, score honestly and document root cause.

---

## Scenario 1: multi_doc_context

**Setup:**
1. `create_session` name="Rerun: multi_doc_context" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md" session_id=<session_id>
3. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md" session_id=<session_id>

**Turns:**
- Turn 1: "What was the Q3 2025 revenue and year-over-year growth for Acme Corp?"
  - Ground truth: $14.2 million, 23% YoY growth
  - CRITICAL FAIL if wrong figures or if agent uses employee_handbook.md instead
- Turn 2: "What is the remote work policy?"
  - Ground truth: 3 days/week with manager approval; fully remote requires VP-level approval
  - Agent should use employee_handbook.md, NOT acme_q3_report.md
  - Penalize if agent appends Q3 financial data to this answer
- Turn 3: "What is the CEO's outlook for Q4 mentioned in that financial report?"
  - Ground truth: "15-18% growth driven by enterprise segment expansion and three new product launches planned for November"
  - KEY TEST: "that financial report" = acme_q3_report.md — agent must resolve correctly
  - CRITICAL FAIL if handbook data mixed in

**Previous score:** 9.05 — PASS

---

## Scenario 2: cross_section_rag

**Setup:**
1. `create_session` name="Rerun: cross_section_rag" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md" session_id=<session_id>

**Turns:**
- Turn 1: "Give me a complete financial summary of Acme Corp's Q3 performance and what to expect in Q4."
  - Ground truth: Revenue $14.2M, 23% YoY growth, Q4 outlook 15-18% growth driven by enterprise segment expansion
  - CRITICAL FAIL if: wrong revenue figure, hallucinated margins/percentages, references employee_handbook.md
  - Fix 3 should prevent this — only acme_q3_report.md should be visible
- Turn 2: "If Q4 hits the low end of that projection, what would be the full-year 2025 revenue?"
  - Ground truth: Q4 at 15% growth = $14.2M × 1.15 = ~$16.3M; full-year requires Q1+Q2+Q3+Q4 but Q1/Q2 not in document — agent should note this limitation
- Turn 3: "What exact words did the CEO use about Q4?"
  - Ground truth: "For Q4, we project 15-18% growth driven by enterprise segment expansion and three new product launches planned for November."
  - Both required phrases: "15-18%" AND "enterprise segment"
  - CRITICAL FAIL if quote is fabricated

**Previous score:** 6.67 — FAIL (Fix 3 should now resolve Turn 1 hallucination)
**Expected improvement:** Turn 1 should now PASS since only acme_q3_report.md is visible

---

## Scenario 3: negation_handling

**Setup:**
1. `create_session` name="Rerun: negation_handling" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md" session_id=<session_id>

**Turns:**
- Turn 1: "Are contractors eligible for health benefits?"
  - Ground truth: NO — contractors are NOT eligible. Benefits for full-time employees only (Sections 3 + 5)
  - CRITICAL FAIL if agent says contractors ARE eligible
- Turn 2: "What benefits or perks are contractors eligible for, if any?"
  - Ground truth: No contractor benefits listed. EAP applies to "all employees" but contractors are NOT classified as employees per Section 3.
  - KEY TEST for Fix 1: Does agent use correct path? Previous runs used wrong path "C:/Users/14255/employee_handbook.md"
  - Fix 1 (fuzzy basename fallback) should resolve path automatically in ≤3 tool calls
  - Score fix1_validated: true if Turn 2 completes correctly in ≤3 tool calls
- Turn 3: "What about part-time employees — are they eligible for benefits?"
  - Ground truth: Part-time employees NOT eligible for health/dental/vision (Section 5 explicit). EAP access only. Not full benefits.
  - Previous: FAILED (INCOMPLETE_RESPONSE — agent never gave an answer)

**Previous score:** 4.62 — FAIL (fix_phase score: 8.10)
**Expected improvement:** Fix 1 should prevent path resolution failures, Fix 3 ensures clean session context

---

## Scenario 4: table_extraction

**Setup:**
1. `create_session` name="Rerun: table_extraction" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/sales_data_2025.csv" session_id=<session_id>

**Known limitation:** The CSV (~500 rows) is indexed into only 2 RAG chunks. Full aggregation is not possible via RAG alone. Agent should attempt all queries and acknowledge data limitations honestly.

**Turns:**
- Turn 1: "What was the best-selling product in March 2025 by revenue?"
  - Ground truth: Widget Pro X (~$45,000 for March, but CSV chunks may not include March)
  - PASS criterion: Agent names Widget Pro X (even if acknowledging limited data). No CRITICAL FAIL for honest "March data not visible in indexed chunks"
- Turn 2: "What was the total Q1 2025 revenue across all products?"
  - Ground truth: $342,150 (full dataset). Agent will likely see only partial data.
  - PASS criterion: Agent provides whatever total it can from visible chunks AND clearly states data is partial/incomplete
  - CRITICAL FAIL if agent presents a partial total as the definitive full total without caveat
- Turn 3: "Who was the top salesperson by total revenue in Q1?"
  - Ground truth: Sarah Chen at $70,000
  - PASS criterion: Agent either names Sarah Chen OR acknowledges it cannot determine this from partial RAG data
  - CRITICAL FAIL if agent names someone else confidently without caveat

**Previous score:** 5.17 — FAIL (CSV chunking limitation)
**Note:** This is a known architectural limitation. Honest acknowledgment of data incompleteness earns partial credit.

---

## After All 4 Scenarios:

For each scenario, write JSON to `eval/results/rerun/<scenario_id>.json`:
```json
{
  "scenario_id": "...",
  "run": "rerun",
  "previous_score": X.XX,
  "status": "PASS or FAIL",
  "overall_score": X.XX,
  "turns": [...],
  "improvement": "improved/no_change/regressed",
  "notes": "...",
  "fix_validated": {
    "fix1_basename_fallback": true/false/null,
    "fix2_verbosity": null,
    "fix3_session_isolation": true/false/null
  }
}
```

Append to `eval/eval_run_report.md`:
```
### [TIMESTAMP] Batch 2 Results
| Scenario | Prev | New | Delta | Status |
...
```

Print "BATCH 2 COMPLETE" when all 4 done.
