# GAIA Agent Eval — Fix Phase

Read this entire file before starting. Execute all steps in order.

## Context

We ran all 23 eval scenarios. Results are in:
- `eval/eval_run_report.md` — full run log with analysis
- `eval/results/phase3/` — JSON results for Phase 3 scenarios

## 3 High-Priority Fixes to Implement

### Fix 1 (P0): Path Truncation Bug in query_specific_file
**Failing scenarios**: negation_handling (4.62), cross_section_rag (6.67), vague_request_clarification T3

**Root cause**: After Turn 1 succeeds with a bare filename, the agent constructs a wrong absolute path like `C:\Users\14255\employee_handbook.md`. The `query_specific_file` tool fails because it requires an exact path match.

**Fix target**: `src/gaia/mcp/servers/agent_ui_mcp.py`

In the `query_specific_file` tool handler, after the document lookup fails for the provided path, add fuzzy basename fallback:
1. Extract the basename from the provided path (e.g. `employee_handbook.md`)
2. Search the database for indexed documents whose path ends with that basename
3. If exactly 1 match is found, use that document instead and proceed normally
4. If 0 or 2+ matches, return a helpful error message

Read the file first to understand its structure, then make this targeted change.

---

### Fix 2 (P1): Verbosity Calibration in Agent System Prompt
**Failing scenario**: concise_response (7.15) — Turn 2 gave 84-word wall for "Revenue?" (one-word question)

**Root cause**: No instruction in the system prompt about proportional response length.

**Fix target**: `src/gaia/agents/chat/agent.py` (the SYSTEM_PROMPT or equivalent system prompt string)

Add this sentence to the system prompt (find the appropriate location, likely near the "personality" or "response style" section, or at the end of the existing prompt):

```
Match your response length to the complexity of the question. For short questions, greetings, or simple factual lookups, reply in 1-2 sentences. Only expand to multiple paragraphs for complex analysis requests.
```

Read the file first to find the exact system prompt location and where to insert this.

---

### Fix 3 (P1): list_indexed_documents Cross-Session Contamination
**Failing scenarios**: honest_limitation T3, csv_analysis, smart_discovery (contributed to false PASS in first run)

**Root cause**: `list_indexed_documents` returns ALL documents from the global library, not just documents indexed in the current session. This causes fresh sessions to "see" documents from prior sessions.

**Fix target**: `src/gaia/mcp/servers/agent_ui_mcp.py`

In the `list_indexed_documents` tool handler, filter results to only documents that belong to the current session_id. Read the file to understand how session_id is tracked in the MCP context and how documents are stored in the database.

---

## Execution Steps

### Step 1: Read context files
1. Read `eval/results/phase3/negation_handling.json`
2. Read `eval/results/phase3/concise_response.json`
3. Read `eval/results/phase3/cross_section_rag.json`
4. Read `src/gaia/mcp/servers/agent_ui_mcp.py`
5. Read `src/gaia/agents/chat/agent.py`

### Step 2: Apply the 3 fixes
Make the targeted changes described above. Do NOT rewrite entire files — make minimal, surgical edits.

### Step 3: Write fix log
Write `eval/results/fix_phase/fix_log.json`:
```json
[
  {
    "fix_id": 1,
    "file": "src/gaia/mcp/servers/agent_ui_mcp.py",
    "change_summary": "...",
    "targets_scenario": ["negation_handling", "cross_section_rag"],
    "rationale": "..."
  },
  ...
]
```

### Step 4: Re-run scenario — negation_handling
Use gaia-agent-ui MCP tools:
1. Call `system_status` to verify Agent UI is running on :4200
2. Call `create_session` with name "Fix Rerun: negation_handling"
3. Call `index_document` with ABSOLUTE path to `C:/Users/14255/Work/gaia4/eval/corpus/documents/employee_handbook.md`
4. Send Turn 1: "Who is NOT eligible for health benefits and retirement benefits?"
   - Ground truth: Contractors are NOT eligible. Only full-time employees qualify.
   - Score correctness (0-10): Did it say contractors are excluded? No hedging?
5. Send Turn 2: "What about dental and vision coverage?"
   - Ground truth: Same policy — full-time employees only, contractors excluded
   - Score (0-10)
6. Send Turn 3: "So if I'm a full-time employee, I get all of these benefits?"
   - Ground truth: Yes — full-time employees get health, dental, vision, retirement
   - Score (0-10)
7. Call `delete_session`
8. Write result to `eval/results/fix_phase/negation_handling_rerun.json` with this structure:
```json
{
  "scenario_id": "negation_handling",
  "run": "fix_phase",
  "original_score": 4.62,
  "status": "PASS or FAIL",
  "overall_score": X.XX,
  "turns": [...per-turn details with scores...],
  "improvement": "improved/no_change/regressed",
  "notes": "..."
}
```

### Step 5: Re-run scenario — concise_response
1. Create a new session "Fix Rerun: concise_response"
2. Index `C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md`
3. Send Turn 1: "Hi"
   - Ground truth: ≤5 words, no tools used (e.g. "Hey! What are you working on?")
   - Score (0-10): PASS only if ≤2 sentences
4. Send Turn 2: "Revenue?"
   - Ground truth: ~"$14.2M" or "Q3 revenue was $14.2 million" — 1 short sentence
   - Score (0-10): FAIL if >2 sentences or if agent deflects with clarifying questions
5. Send Turn 3: "Was it a good quarter?"
   - Ground truth: "Yes — 23% YoY growth to $14.2M" (≤3 sentences)
   - Score (0-10): FAIL if >4 sentences
6. Call `delete_session`
7. Write result to `eval/results/fix_phase/concise_response_rerun.json`

### Step 6: Re-run scenario — cross_section_rag
1. Create new session "Fix Rerun: cross_section_rag"
2. Index `C:/Users/14255/Work/gaia4/eval/corpus/documents/acme_q3_report.md` ONLY (no handbook)
3. Send Turn 1: "Give me a complete picture of Acme's Q3 performance — revenue, growth, and CEO outlook all in one answer"
   - Ground truth: $14.2M revenue, 23% YoY growth, 15-18% Q4 outlook (all from acme_q3_report.md)
   - Score (0-10): FAIL if any wrong document data used or hallucinated figures
4. Send Turn 2: "What does that mean for their Q4 projected revenue in dollars?"
   - Ground truth: 15-18% growth on $14.2M = ~$16.3M-$16.7M range
   - Score (0-10)
5. Send Turn 3: "Quote me exactly what the CEO said about the outlook"
   - Ground truth: "15-18% growth driven by enterprise segment expansion and three new product launches planned for November"
   - Score (0-10)
6. Call `delete_session`
7. Write result to `eval/results/fix_phase/cross_section_rag_rerun.json`

### Step 7: Write summary
Write `eval/results/fix_phase/summary.md`:
```markdown
# Fix Phase Summary

## Fixes Applied
[list of 3 fixes with files changed]

## Before/After Scores
| Scenario | Before | After | Delta | Status |
|----------|--------|-------|-------|--------|
| negation_handling | 4.62 | X.XX | +X.XX | improved/same/regressed |
| concise_response | 7.15 | X.XX | +X.XX | ... |
| cross_section_rag | 6.67 | X.XX | +X.XX | ... |

## Assessment
[Which fixes worked, which didn't, what still needs work]
```

## IMPORTANT RULES
- Do NOT commit any changes
- Do NOT run npm build or restart servers
- Do NOT create new directories beyond `eval/results/fix_phase/`
- The Agent UI is already running on :4200
- Use absolute paths for index_document calls: `C:/Users/14255/Work/gaia4/eval/corpus/documents/`
- After ALL steps complete, print "FIX PHASE COMPLETE"
