# Eval Scenario: topic_switch

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\topic_switch.json`

## Ground Truth
File A: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
- Q3 revenue: $14.2 million

File B: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`
- PTO for first-year employees: 15 days

## Scenario Purpose
Test whether the agent stays grounded when the user **rapidly switches topics** mid-conversation.
The agent must track which document is relevant to each question WITHOUT mixing up facts
from different domains.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: topic_switch")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
4. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`

5. **Turn 1 (financial question)** — Call `send_message(session_id, "What was Acme's Q3 revenue?")`
   - Expected: "$14.2 million" from acme_q3_report.md

6. **Turn 2 (abrupt switch to HR)** — Call `send_message(session_id, "Wait, actually — how many PTO days do new employees get?")`
   - Expected: "15 days" from employee_handbook.md
   - CRITICAL FAIL if agent answers with financial data

7. **Turn 3 (switch back to finance)** — Call `send_message(session_id, "OK back to the financials — what was the YoY growth?")`
   - Expected: "23%" from acme_q3_report.md
   - CRITICAL FAIL if agent answers with HR/PTO data

8. **Turn 4 (ambiguous — could be either)** — Call `send_message(session_id, "How does that compare to expectations?")`
   - "that" refers to the 23% YoY growth from Turn 3 context
   - Expected: Agent refers to Q4 outlook (15-18% projected) or compares 23% to industry benchmarks
   - Score context_retention=10 if agent correctly links "that" to the financial topic from Turn 3
   - Score context_retention=2 if agent switches back to HR topic

9. Call `get_messages(session_id)` to capture full trace.

10. Call `delete_session(session_id)` to clean up.

11. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\topic_switch.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 2: HR question answered with financial data
- CRITICAL FAIL Turn 3: Finance question answered with HR data

## Result JSON format
```json
{
  "scenario_id": "topic_switch",
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
  "cost_estimate": {"turns": 4, "estimated_usd": 0.05}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase3/` directory already exists
- 4 turns in this scenario (not the usual 3)
- CRITICAL: agent must not mix up finance and HR facts across rapid topic switches
