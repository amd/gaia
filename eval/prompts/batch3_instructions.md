# Eval Batch 3 — 5 Scenarios

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
1. **Path resolution failure**: Re-send the same question. Fix 1 (basename fallback) should handle it. If still failing after 2 retries, document and move on.
2. **No answer / incomplete response**: Re-send: "Please complete your answer."
3. **Verbose response to short question**: Re-send: "Please give a shorter answer — 1-2 sentences max."
4. **Wrong document used**: Re-send with explicit context: "Please only use [filename] for this answer."
5. **Hallucinated fact**: Re-send: "Please check the document — I believe that figure may be incorrect."
After applying a fix, re-score that turn with the corrected response and note "fix_applied: true" in the JSON.
Maximum 2 fix attempts per turn. If still failing after 2 attempts, score honestly and document root cause.

---

## Scenario 1: csv_analysis

**Setup:**
1. `create_session` name="Rerun: csv_analysis" → note session_id
2. `index_document` filepath="C:/Users/14255/Work/gaia4/eval/corpus/documents/sales_data_2025.csv" session_id=<session_id>
NOTE: Do NOT also index acme_q3_report.md — Fix 3 means only the CSV should be visible to this session.

**Turns:**
- Turn 1: "Which region had the highest sales in Q1 2025?"
  - Ground truth: North America led sales. CSV has transaction-level data that may not aggregate cleanly via RAG.
  - PASS if agent provides North America OR honestly says "CSV chunks don't show regional aggregation"
  - CRITICAL FAIL if agent cites Q3 acme_q3_report.md regional data as Q1 CSV data
- Turn 2: "Can you give me a complete breakdown of all sales by product?"
  - Ground truth: Widget Pro X is primary product (~$600/unit). Full breakdown requires processing all 500 rows.
  - PASS if agent describes what it CAN see from CSV chunks + acknowledges incompleteness
  - CRITICAL FAIL if agent presents Q3 acme_q3_report.md product breakdown as if it's the CSV answer
- Turn 3: "OK, just tell me what you CAN see from the data you have."
  - Ground truth: Agent describes transaction-level CSV data: Widget Pro X, unit prices ($400-$600), regions, sales reps. Notes it cannot aggregate all 500 rows.
  - PASS if agent accurately describes what's visible from the 2 CSV chunks

**Previous score:** 6.2 — PASS (barely; many near-failures)
**Fix 3 expected improvement:** Should prevent acme_q3_report.md contamination since only CSV is session-linked

---

## Scenario 2: known_path_read

**Setup:**
1. `create_session` name="Rerun: known_path_read" → note session_id
2. Do NOT pre-index any documents — agent should index on demand when given the path

**Turns:**
- Turn 1: "Please read the file at C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\meeting_notes_q3.txt and tell me when the next meeting is."
  - Ground truth: October 15, 2025 at 2:00 PM PDT, Conference Room B and Zoom
  - Expected tool flow: index_document with given path, then query_specific_file
  - PASS if correct date/time returned
- Turn 2: "What were the action items discussed in that meeting?"
  - Ground truth: Raj Patel → finalize pipeline data by Oct 7; Sandra Kim → confirm QA timeline by Oct 10; All VPs → submit Q4 OKR check-ins to Jane Smith by Oct 14; decisions: Q4 launch dates locked, if Salesforce slips mobile app delays instead, API deprecation plan by Nov 1
  - "that meeting" = meeting_notes_q3.txt from Turn 1
- Turn 3: "Now read C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\acme_q3_report.md and tell me the Q3 revenue."
  - Ground truth: $14.2 million, 23% YoY growth
  - Agent should index the new file and query it

**Previous score:** 8.98 — PASS

---

## Scenario 3: no_tools_needed

**Setup:**
1. `create_session` name="Rerun: no_tools_needed" → note session_id
2. Do NOT index any documents

**Turns:**
- Turn 1: "Hello! What can you help me with?"
  - Ground truth: Friendly greeting + capability description. ZERO tool calls.
  - CRITICAL FAIL if agent calls any tools for a greeting
- Turn 2: "What is the capital of France?"
  - Ground truth: "Paris" — from general knowledge. ZERO tool calls.
  - CRITICAL FAIL if agent calls any tools (no documents needed for this)
- Turn 3: "What is 15% of 200?"
  - Ground truth: "30" — arithmetic. ZERO tool calls.
  - CRITICAL FAIL if agent calls any tools

**Previous score:** 9.7 — PASS

---

## Scenario 4: search_empty_fallback

**Setup:**
1. `create_session` name="Rerun: search_empty_fallback" → note session_id
2. Do NOT pre-index any documents — scenario tests discovery behavior

**Known issue:** The api_reference.py file is at C:/Users/14255/Work/gaia4/eval/corpus/documents/api_reference.py
The original run failed because agent searched for "*.md" but never tried "*.py".

**Turns:**
- Turn 1: "How does the API handle authentication? I think there's documentation somewhere."
  - Ground truth: Bearer token via Authorization header (from api_reference.py)
  - KEY TEST: Does agent attempt browse_files on eval/corpus/documents/ or search with "*.py" pattern?
  - PASS if agent discovers api_reference.py. PASS with penalty if agent searches but fails to find it and honestly acknowledges this.
  - CRITICAL FAIL only if agent fabricates authentication details
- Turn 2: "What endpoints are available in that API?"
  - Ground truth: /v1/chat/completions, /v1/models, /health (from the api_reference.py or from src/gaia/api/)
  - PASS if agent finds and describes real endpoints
- Turn 3: "Find documentation about the XYZ authentication protocol."
  - Ground truth: XYZ protocol does NOT exist. Agent should search, find nothing, and clearly say "not found" without fabricating XYZ details.
  - CRITICAL FAIL if agent invents XYZ protocol details

**Previous score:** 5.32 — FAIL (Turn 1 never found api_reference.py)

---

## Scenario 5: file_not_found

**Setup:**
1. `create_session` name="Rerun: file_not_found" → note session_id
2. Do NOT pre-index any documents

**Turns:**
- Turn 1: "Please read and summarize C:\\Users\\14255\\Documents\\quarterly_report_2024.pdf"
  - Ground truth: File does NOT exist. Agent should attempt to read/index, get an error, then gracefully say file not found and offer alternatives.
  - CRITICAL FAIL if agent fabricates content for the non-existent PDF
- Turn 2: "Try C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\emplyee_handbook.md"
  - Note: "emplyee" is a TYPO (missing 'o'). Correct name is "employee_handbook.md"
  - Ground truth: File not found at typo path; agent should find "employee_handbook.md" nearby and summarize it
  - PASS if agent handles the typo gracefully and finds/summarizes the correct file
- Turn 3: "OK, try C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\employee_handbook.md"
  - Ground truth: Correct path. Agent should read and provide a detailed summary.
  - Expected content: employment types (full-time/part-time/contractors), PTO (15 days first year), benefits (health/dental/vision for full-time), remote work (3 days/week with approval)

**Previous score:** 9.27 — PASS

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
  "turns": [...],
  "improvement": "improved/no_change/regressed",
  "notes": "..."
}
```

Append to `eval/eval_run_report.md`:
```
### [TIMESTAMP] Batch 3 Results
| Scenario | Prev | New | Delta | Status |
...
```

Print "BATCH 3 COMPLETE" when all 5 done.
