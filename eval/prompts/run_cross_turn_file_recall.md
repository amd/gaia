# Eval Scenario: cross_turn_file_recall

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase2\cross_turn_file_recall.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\product_comparison.html`

Known facts:
- StreamLine: **$49/month**
- ProFlow: **$79/month**
- Price difference: **$30/month** (ProFlow more expensive)
- Integrations: StreamLine 10, ProFlow 25
- Ratings: StreamLine 4.2/5, ProFlow 4.7/5
- Verdict: StreamLine = budget choice; ProFlow = better integrations + ratings but $30 more

## Scenario Purpose
Test whether the agent recalls the indexed document across turns WITHOUT the user re-mentioning its name.
- Turn 1: establishes what is indexed (agent lists documents)
- Turn 2: asks about pricing without naming the file — agent must use indexed context
- Turn 3: follow-up "which one is better value for money?" without naming either product

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: cross_turn_file_recall")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\product_comparison.html`
   - Check chunk_count > 0. If 0 or error → write status="SETUP_ERROR" and stop.

4. **Turn 1** — Call `send_message(session_id, "What documents do you have access to?")`
   - Expected: Agent lists or acknowledges product_comparison.html (or similar name)
   - PASS if agent acknowledges the indexed document exists
   - Score context_retention = 10 (first turn, auto)
   - Score correctness = 10 if agent correctly identifies the document

5. **Turn 2 (cross-turn recall test)** — Call `send_message(session_id, "How much do the two products cost?")`
   - Note: User did NOT mention a filename or document. Agent must recall what was indexed.
   - Expected: Agent states StreamLine $49/month and ProFlow $79/month
   - CRITICAL FAIL if agent says it doesn't know what products the user is referring to (failure to recall)
   - PASS if agent uses indexed document to answer without the user re-mentioning the filename
   - Score context_retention highly if agent used session context to answer without user re-specifying the doc
   - Score context_retention=2 if agent asked "which document?" or failed to recall

6. **Turn 3 (pronoun + value judgment)** — Call `send_message(session_id, "Which one is better value for money?")`
   - Note: "which one" refers to the two products discussed in Turn 2
   - Expected: Agent answers based on the indexed document (StreamLine = budget, ProFlow = more features)
   - PASS if agent resolves "which one" and answers from document context without hallucinating
   - Score correctness based on whether the answer is grounded in the document's verdict section

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase2\cross_turn_file_recall.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- Turn 1: context_retention = 10 automatically (first turn, N/A)
- Turn 2: context_retention = critical — did agent recall indexed doc without user re-mentioning it?
  - context_retention=10: answered using indexed doc without asking user to specify
  - context_retention=5: answered correctly but required an extra tool call to rediscover the doc
  - context_retention=2: asked user "which document?" or "which products?" (failure)
- Turn 3: context_retention = did agent resolve "which one" using Turn 2 context?
- CRITICAL FAIL: Turn 2 agent says it cannot answer because it doesn't know what products the user means

## Result JSON format
```json
{
  "scenario_id": "cross_turn_file_recall",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "What documents do you have access to?",
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
- The `eval/results/phase2/` directory already exists
- Be honest: if agent fails to recall context across turns, score context_retention=2 not 7
- The Turn 2 test is the CRITICAL one: "how much do the two products cost?" with NO filename given
