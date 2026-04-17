# Eval Scenario: vague_request_clarification

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\vague_request_clarification.json`

## Ground Truth
Files to index:
- `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
- `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`

## Scenario Purpose
Test whether the agent **asks for clarification** when a request is ambiguous
(multiple documents indexed, vague reference). The agent should NOT guess and
pick an arbitrary document — it should ask which one the user means.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: vague_request_clarification")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

4. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

5. **Turn 1 (vague request — multiple docs)** — Call `send_message(session_id, "Summarize the document.")`
   - Two documents are indexed — agent doesn't know which one
   - Expected: Agent asks for clarification ("Which document? I have acme_q3_report.md and employee_handbook.md indexed")
   - CRITICAL FAIL if agent picks one arbitrarily and summarizes it without asking
   - PASS if agent asks the user which document to summarize
   - Score tool_selection=10 if agent asks for clarification before querying
   - Score tool_selection=2 if agent picks arbitrarily without asking

6. **Turn 2 (clarification provided)** — Call `send_message(session_id, "The financial report.")`
   - User clarified: they mean acme_q3_report.md (it's the financial report)
   - Expected: Agent now summarizes acme_q3_report.md with Q3 financial data
   - Score correctness=10 if summary includes "$14.2 million" or "23% growth"
   - CRITICAL FAIL if agent summarizes employee_handbook instead of the financial report

7. **Turn 3 (follow-up on second doc)** — Call `send_message(session_id, "Now summarize the other one.")`
   - "the other one" refers to employee_handbook.md
   - Expected: Agent summarizes employee_handbook.md (PTO, benefits, remote work)
   - Score context_retention=10 if agent correctly resolves "the other one" to employee_handbook.md
   - Score correctness=10 if summary includes PTO, benefits, or remote work policy

8. Call `get_messages(session_id)` to capture full trace.

9. Call `delete_session(session_id)` to clean up.

10. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\vague_request_clarification.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 1: agent arbitrarily picks a document and summarizes without asking
- CRITICAL FAIL Turn 2: agent summarizes employee_handbook instead of acme_q3_report
- CRITICAL FAIL Turn 3: agent summarizes acme_q3_report instead of employee_handbook

## Result JSON format
```json
{
  "scenario_id": "vague_request_clarification",
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
- Turn 1 CRITICAL: agent must ask which document, NOT pick one arbitrarily
- Turn 2: agent must pick acme_q3_report.md (the financial one) after user says "financial report"
- Turn 3: "the other one" = employee_handbook.md
