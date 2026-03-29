# Eval Scenario: multi_doc_context

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\multi_doc_context.json`

## Ground Truth
File A: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
- Q3 revenue: $14.2 million
- YoY growth: 23%
- Q4 outlook: 15-18% growth

File B: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`
- PTO: 15 days for first-year employees
- Remote work: up to 3 days/week with manager approval
- Contractors: NOT eligible for benefits

## Scenario Purpose
Test whether the agent keeps two simultaneously indexed documents straight.
- Turn 1: Ask about File A (financial data)
- Turn 2: Ask about File B (HR policy)
- Turn 3: Ask about File A again using a pronoun ("that report")
- CRITICAL: Agent must NOT confuse facts from A with facts from B

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: multi_doc_context")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

4. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

5. **Turn 1 (File A question)** — Call `send_message(session_id, "What was the Q3 2025 revenue and year-over-year growth for Acme Corp?")`
   - Expected: Agent answers from acme_q3_report.md — $14.2M, 23% YoY growth
   - CRITICAL FAIL if agent answers with HR/handbook facts
   - Score correctness=10 if "$14.2 million" and "23%" both present

6. **Turn 2 (File B question)** — Call `send_message(session_id, "What is the remote work policy?")`
   - Expected: Agent answers from employee_handbook.md — 3 days/week with manager approval
   - CRITICAL FAIL if agent mixes up with financial data
   - Score correctness=10 if "3 days" or "3 days per week" and "manager approval" present

7. **Turn 3 (Back to File A with pronoun)** — Call `send_message(session_id, "What is the CEO's outlook for Q4 mentioned in that financial report?")`
   - Expected: Agent returns to acme_q3_report.md — "15-18% growth driven by enterprise segment expansion"
   - CRITICAL FAIL if agent answers with handbook data
   - Score context_retention=10 if agent correctly identifies "that financial report" = acme_q3_report.md

8. Call `get_messages(session_id)` to capture full trace.

9. Call `delete_session(session_id)` to clean up.

10. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\multi_doc_context.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Agent uses handbook facts to answer financial questions or vice versa (document confusion)
- context_retention=10 in Turn 3 if agent correctly resolves "that financial report" to acme_q3_report.md

## Result JSON format
```json
{
  "scenario_id": "multi_doc_context",
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
- The `eval/results/phase3/` directory must be created if it doesn't exist
- CRITICAL TEST: agent must NOT confuse facts from the two different documents
- Turn 3 is the key multi-doc test: agent must return to the financial report, not the handbook
