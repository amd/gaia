# Eval Scenario: negation_handling

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\negation_handling.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`

Known facts:
- Health benefits: Full-time employees only (Section 5: Benefits)
- Contractors: NOT eligible for health benefits
- PTO: Also full-time employees only
- Remote work: employees may work up to 3 days/week with manager approval

## Scenario Purpose
Test whether the agent correctly handles **negation** — "who is NOT eligible?"
The agent must give a definitive negative answer, not hedge with "it depends" or answer the wrong polarity.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: negation_handling")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

4. **Turn 1 (negation test)** — Call `send_message(session_id, "Are contractors eligible for health benefits?")`
   - Expected: Agent answers NO — contractors are NOT eligible, benefits are for full-time employees only
   - CRITICAL FAIL if agent says "yes" or "contractors may be eligible"
   - CRITICAL FAIL if agent gives a hedged non-answer ("it depends on the contractor type") when the document is definitive
   - Score correctness=10 if response clearly states contractors are NOT eligible
   - Score correctness=4 if agent gives a hedged answer without committing to NO
   - Score correctness=0 if agent says contractors ARE eligible

5. **Turn 2 (follow-up: what are they eligible for?)** — Call `send_message(session_id, "What benefits or perks are contractors eligible for, if any?")`
   - Expected: Agent states contractors have no listed benefits in the handbook (or that no benefits are explicitly listed for contractors)
   - CRITICAL FAIL if agent invents contractor benefits not in the document
   - Score correctness=10 if agent says no contractor benefits are listed / none mentioned in handbook
   - Score correctness=5 if agent hedges but doesn't fabricate

6. **Turn 3 (scope check)** — Call `send_message(session_id, "What about part-time employees — are they eligible for benefits?")`
   - Expected: Agent answers based on the document. If document says full-time only, answer is that part-time employees are NOT eligible (same exclusion as contractors).
   - If the document doesn't explicitly address part-time, agent should say it's not specified (NOT make up an answer).
   - CRITICAL FAIL if agent invents part-time benefit eligibility not in the document

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\negation_handling.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 1: agent says contractors ARE eligible for benefits
- CRITICAL FAIL Turn 2: agent fabricates contractor benefits not in document
- CRITICAL FAIL Turn 3: agent fabricates part-time employee benefits not in document

## Result JSON format
```json
{
  "scenario_id": "negation_handling",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "...",
      "agent_response": "...",
      "agent_tools": ["tool1"],
      "scores": {"correctness": 0-10, "tool_selection": 0-10, "context_retention": 0-10, "completeness": 0-10, "efficiency": 0-10, "personality": 0-10, "error_recovery": 0-10},
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "short explanation"
    }
  ],
  "root_cause": null,
  "recommended_fix": null,
  "cost_estimate": {"turns": 3, "estimated_usd": 0.04}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase3/` directory already exists
- The key test is Turn 1: agent must say contractors are NOT eligible. A hedge is a partial failure. A yes is a critical fail.
- If the employee_handbook.md does not mention part-time explicitly, agent should say "not specified" for Turn 3.
