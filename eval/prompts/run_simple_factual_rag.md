# Eval Scenario: simple_factual_rag

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase2\simple_factual_rag.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`

Known facts:
- Turn 1: Q3 2025 revenue = **$14.2 million**
- Turn 2: Year-over-year growth = **23% increase from Q3 2024's $11.5 million**
- Turn 3: CEO Q4 outlook = **Projected 15-18% growth driven by enterprise segment expansion**

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: simple_factual_rag")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
   - Wait for response. Check chunk_count > 0.
   - If chunk_count = 0 or error → write status="SETUP_ERROR" and stop.

4. **Turn 1** — Call `send_message(session_id, "What was Acme Corp's Q3 2025 revenue?")`
   - Record full response + agent_steps
   - Judge: Did agent state "$14.2 million"? Score correctness 0-10.
   - Compute overall score using weights: correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05

5. **Turn 2** — Call `send_message(session_id, "What was the year-over-year revenue growth?")`
   - Record full response + agent_steps
   - Judge: Did agent mention 23% and/or $11.5M baseline? Score all dimensions.

6. **Turn 3** — Call `send_message(session_id, "What's the CEO's outlook for Q4?")`
   - Record full response + agent_steps
   - Judge: Did agent mention 15-18% projected growth? Score all dimensions.

7. Call `get_messages(session_id)` to capture full persisted trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase2\simple_factual_rag.json`:

```json
{
  "scenario_id": "simple_factual_rag",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "...",
      "agent_response": "...",
      "agent_tools": ["tool1"],
      "scores": {
        "correctness": 0-10,
        "tool_selection": 0-10,
        "context_retention": 0-10,
        "completeness": 0-10,
        "efficiency": 0-10,
        "personality": 0-10,
        "error_recovery": 0-10
      },
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "short explanation"
    }
  ],
  "root_cause": null,
  "recommended_fix": null,
  "cost_estimate": {"turns": 3, "estimated_usd": 0.05}
}
```

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- For Turn 1: context_retention is N/A (first turn) — score 10 automatically
- For Turn 2-3: context_retention tests if agent uses prior context

## IMPORTANT
- Use absolute Windows paths with backslashes for file operations
- Make the `eval/results/phase2/` directory if it doesn't exist
- Do NOT skip judging — score all 7 dimensions for every turn
- Be honest: if the agent gave a wrong answer, score correctness=0 not 5
