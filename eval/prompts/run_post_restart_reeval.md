# GAIA Agent Eval — Post-Restart Re-Eval

Read this entire file before starting. Execute all steps in order.

## Context

The GAIA backend server was restarted. Three code fixes are now LIVE:
- Fix 1: Fuzzy basename fallback in `query_specific_file` (`rag_tools.py`)
- Fix 2: Proportional response length in system prompt (`agent.py`)
- Fix 3: Session isolation — `_resolve_rag_paths` returns `([], [])` when no document_ids (`_chat_helpers.py`)

Previous fix phase scores (server was NOT restarted):
- concise_response: 7.00 FAIL (Fixes 2+3 not active)
- negation_handling: 8.10 PASS (Fix 1 not active, agent recovered manually)

**CRITICAL NOTE on Fix 3:** Fix 3 means a session with no `document_ids` will give the agent an EMPTY document context. To make documents visible to the agent, you MUST pass the `session_id` parameter when calling `index_document`. This links the document to the session's `document_ids` so the agent can see it.

## IMPORTANT RULES
- Do NOT commit any changes
- Do NOT restart servers
- **DO NOT call `delete_session` on ANY session** — conversations must be preserved
- ALWAYS pass `session_id` when calling `index_document` — required for Fix 3 compatibility
- Use absolute paths for index_document: `C:/Users/14255/Work/gaia4/eval/corpus/documents/`
- After ALL steps complete, print "POST-RESTART RE-EVAL COMPLETE"

---

## Task: Re-run 2 scenarios and score them

### Step 1: Verify server is running
Call `system_status` — confirm Agent UI is on :4200.

---

### Step 2: Re-run concise_response (Fix 2 + Fix 3 now active)

1. Create session: "Post-Restart: concise_response"
   - Note the session_id returned
2. Index document WITH session_id:
   - filepath: `C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md`
   - session_id: [the session_id from step 1]
   - This links the doc to the session so the agent can see it (required for Fix 3)
3. Send Turn 1: "Hi"
   - Ground truth: ≤5 words, no tools. E.g. "Hey! What are you working on?"
   - Score 0-10: PASS only if ≤2 sentences, no tools called
4. Send Turn 2: "Revenue?"
   - Ground truth: ~"$14.2M" or "Q3 revenue was $14.2 million" — 1 short sentence
   - Score 0-10: FAIL if >2 sentences OR agent deflects with clarifying questions OR mentions wrong doc (sales_data_2025.csv)
   - Fix 2 should prevent the 84-word deflection. Fix 3 prevents sales_data_2025.csv from appearing.
5. Send Turn 3: "Was it a good quarter?"
   - Ground truth: Yes — 23% YoY growth to $14.2M (≤3 sentences)
   - Score 0-10: FAIL if >4 sentences
6. DO NOT delete the session
7. Write result to `eval/results/fix_phase/concise_response_post_restart.json`:
```json
{
  "scenario_id": "concise_response",
  "run": "post_restart",
  "original_score": 7.15,
  "fix_phase_score": 7.00,
  "status": "PASS or FAIL",
  "overall_score": X.XX,
  "turns": [...],
  "improvement": "improved/no_change/regressed",
  "notes": "..."
}
```

---

### Step 3: Re-run negation_handling (Fix 1 now active)

1. Create session: "Post-Restart: negation_handling"
   - Note the session_id returned
2. Index document WITH session_id:
   - filepath: `C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md`
   - session_id: [the session_id from step 1]
3. Send Turn 1: "Who is NOT eligible for health benefits and retirement benefits?"
   - Ground truth: Contractors/part-time/temporary employees NOT eligible. Only full-time qualify.
   - Score correctness (0-10)
4. Send Turn 2: "What about dental and vision coverage?"
   - Ground truth: Same — full-time only, contractors excluded
   - KEY TEST for Fix 1: Did the agent use wrong absolute path (C:/Users/14255/employee_handbook.md)?
     - If Fix 1 worked: basename fallback resolved it automatically, ≤3 tool calls total
     - If not fixed: agent tried wrong path, had to recover in 9+ steps
   - Score (0-10)
5. Send Turn 3: "So if I'm a full-time employee, I get all of these benefits?"
   - Ground truth: Yes — full-time employees get health, dental, vision, retirement
   - Score (0-10)
6. DO NOT delete the session
7. Write result to `eval/results/fix_phase/negation_handling_post_restart.json`:
```json
{
  "scenario_id": "negation_handling",
  "run": "post_restart",
  "original_score": 4.62,
  "fix_phase_score": 8.10,
  "status": "PASS or FAIL",
  "overall_score": X.XX,
  "turns": [...per-turn details with scores and tool_steps count...],
  "fix1_validated": true/false,
  "fix1_notes": "Did Fix 1 reduce Turn 2 from 9 steps to ≤3?",
  "improvement": "improved/no_change/regressed",
  "notes": "..."
}
```

---

### Step 4: Write post-restart summary
Write `eval/results/fix_phase/post_restart_summary.md`:
```markdown
# Post-Restart Re-Eval Summary

## Scores
| Scenario | Original | Fix Phase | Post-Restart | Total Delta | Status |
|----------|----------|-----------|--------------|-------------|--------|
| concise_response | 7.15 | 7.00 | X.XX | +X.XX | PASS/FAIL |
| negation_handling | 4.62 | 8.10 | X.XX | +X.XX | PASS/FAIL |

## Fix Validation
- Fix 1 (basename fallback): VALIDATED / NOT VALIDATED — [evidence]
- Fix 2 (verbosity): VALIDATED / NOT VALIDATED — [evidence]
- Fix 3 (session isolation): VALIDATED / NOT VALIDATED — [evidence]

## Remaining Failures (not yet fixed)
- smart_discovery: 2.80 — root cause: search_file doesn't scan eval/corpus/documents/
- table_extraction: 5.17 — root cause: CSV not properly chunked for aggregation
- search_empty_fallback: 5.32 — root cause: search returns empty, agent doesn't fall back
```
