# Eval Scenario: conversation_summary

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\conversation_summary.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
- Q3 revenue: $14.2 million
- YoY growth: 23%
- Q4 outlook: 15-18% growth
- Top product: Widget Pro X ($8.1M, 57%)
- Top region: North America ($8.5M, 60%)

## Scenario Purpose
Test whether the agent maintains context across **5+ turns** and can summarize the full conversation.
The history_pairs limit (5 pairs = 10 messages) should be the boundary — verify the agent retains context across the max configured limit.

## Architecture audit baseline
- history_pairs = 5 (from architecture audit: max 5 prior conversation pairs)
- This scenario generates 5 turns + a final summary turn = 6 total turns

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: conversation_summary")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`

4. **Turn 1** — Call `send_message(session_id, "What was Acme's Q3 revenue?")`
   - Expected: $14.2 million

5. **Turn 2** — Call `send_message(session_id, "And the year-over-year growth?")`
   - Expected: 23%

6. **Turn 3** — Call `send_message(session_id, "What's the Q4 outlook?")`
   - Expected: 15-18% growth

7. **Turn 4** — Call `send_message(session_id, "Which product performed best?")`
   - Expected: Widget Pro X ($8.1M, 57%)

8. **Turn 5** — Call `send_message(session_id, "Which region led sales?")`
   - Expected: North America ($8.5M, 60%)

9. **Turn 6 (summary test)** — Call `send_message(session_id, "Summarize everything we've discussed in this conversation.")`
   - Expected: Agent recalls ALL prior turns (revenue, growth, outlook, product, region)
   - This tests history retention across 5 pairs (the architectural limit)
   - CRITICAL FAIL if agent can only recall the last 1-2 turns
   - Score context_retention=10 if agent mentions ALL 5 facts: $14.2M, 23%, 15-18%, Widget Pro X, North America
   - Score context_retention=7 if agent recalls 3-4 facts
   - Score context_retention=3 if agent recalls only 1-2 facts (context window truncation)

10. Call `get_messages(session_id)` to capture full trace.

11. Call `delete_session(session_id)` to clean up.

12. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\conversation_summary.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 6: agent cannot recall any prior context (complete context blindness)
- Turn 6 context_retention score drives the most signal for this scenario

## Result JSON format
```json
{
  "scenario_id": "conversation_summary",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "history_pairs_tested": 5,
  "facts_recalled_in_turn6": ["list", "of", "facts", "mentioned"],
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
  "cost_estimate": {"turns": 6, "estimated_usd": 0.07}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase3/` directory already exists
- 6 turns in this scenario (not the usual 3)
- Track facts_recalled_in_turn6: list each of the 5 ground truth facts that appear in the Turn 6 response
- This tests the architecture limit (history_pairs=5) — if agent only recalls last 2 turns, that's a FAIL
