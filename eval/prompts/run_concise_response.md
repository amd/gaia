# Eval Scenario: concise_response

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\concise_response.json`

## Scenario Purpose
Test whether the agent gives **appropriately concise responses** to simple questions.
A short greeting should get a short reply. A simple lookup question should get a direct answer.
Over-verbose responses to simple questions are a personality failure.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: concise_response")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`

4. **Turn 1 (simple greeting)** — Call `send_message(session_id, "Hi")`
   - Expected: Short greeting response (1-2 sentences MAX). Something like "Hi! How can I help?"
   - FAIL if agent responds with a 5+ sentence introduction listing all capabilities
   - Score personality=10 if response is <= 2 sentences and appropriate
   - Score personality=2 if agent writes a wall of text in response to "Hi"

5. **Turn 2 (simple factual lookup)** — Call `send_message(session_id, "Revenue?")`
   - One-word question — agent should give a direct answer: "$14.2 million" or similar
   - Agent should infer from context that user is asking about the indexed report
   - FAIL if agent responds with a 5+ sentence narrative when a one-liner suffices
   - Score personality=10 if response is <= 3 sentences and includes the number
   - Score personality=4 if agent answers correctly but is verbose (3+ paragraphs)

6. **Turn 3 (simple yes/no)** — Call `send_message(session_id, "Was it a good quarter?")`
   - Expected: Short directional answer + key evidence (e.g., "Yes — 23% YoY growth")
   - FAIL if agent writes a multi-paragraph analysis when a sentence suffices
   - Score personality=10 if response is direct and <= 3 sentences

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\concise_response.json`

## Scoring Notes
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- For each turn, note the approximate word count of the response
- personality score is heavily weighted on response length appropriateness:
  - Turn 1 "Hi": <= 2 sentences = 10, 3-4 sentences = 7, 5+ sentences = 3
  - Turn 2 "Revenue?": <= 2 sentences = 10, 3-5 sentences = 7, 5+ sentences with no extra value = 3
  - Turn 3: <= 3 sentences = 10, 4-6 sentences = 7, full paragraph analysis = 4

## Result JSON format
```json
{
  "scenario_id": "concise_response",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "Hi",
      "agent_response": "...",
      "response_word_count": 0,
      "agent_tools": [],
      "scores": {"correctness": 0-10, "tool_selection": 0-10, "context_retention": 0-10, "completeness": 0-10, "efficiency": 0-10, "personality": 0-10, "error_recovery": 0-10},
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "short explanation including word count assessment"
    }
  ],
  "root_cause": null,
  "recommended_fix": null,
  "cost_estimate": {"turns": 3, "estimated_usd": 0.02}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase3/` directory already exists
- Include response_word_count for each turn — this is the key diagnostic metric
- The personality score should reflect whether response length was appropriate for question complexity
