# Eval Scenario: no_tools_needed

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\no_tools_needed.json`

## Scenario Purpose
Test whether the agent responds DIRECTLY to greetings and general knowledge questions
WITHOUT calling any RAG/file tools. Over-tooling is a failure mode — if a user says
"hello" the agent should say "hello back", not run query_documents.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: no_tools_needed")`
   - Do NOT pre-index any documents

3. **Turn 1 (greeting)** — Call `send_message(session_id, "Hello! What can you help me with?")`
   - Expected: Friendly greeting + description of capabilities. NO tool calls.
   - CRITICAL FAIL if agent calls query_documents, search_file, or any RAG tool for a greeting
   - Score tool_selection=10 if zero tool calls
   - Score tool_selection=0 if any tool called
   - Score personality=10 if response is friendly, concise, and direct

4. **Turn 2 (general knowledge)** — Call `send_message(session_id, "What is the capital of France?")`
   - Expected: "Paris" — direct answer from general knowledge, no tool calls
   - CRITICAL FAIL if agent calls any document tool to answer this
   - Score correctness=10 if agent answers "Paris" correctly
   - Score tool_selection=10 if zero tool calls
   - Score tool_selection=2 if agent searches documents for "capital of France"

5. **Turn 3 (math question)** — Call `send_message(session_id, "What is 15% of 200?")`
   - Expected: "30" — simple calculation, no tool calls needed
   - CRITICAL FAIL if agent calls any document tool to answer this
   - Score correctness=10 if agent answers "30" correctly
   - Score tool_selection=10 if zero tool calls
   - Score personality=8 if answer is concise (not overly verbose for a simple calculation)

6. Call `get_messages(session_id)` to capture full trace.

7. Call `delete_session(session_id)` to clean up.

8. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\no_tools_needed.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Any tool call for greeting, capital city, or simple math question
- Note: context_retention = 10 for all turns (first turn NA, subsequent turns are stateless general knowledge)

## Result JSON format
```json
{
  "scenario_id": "no_tools_needed",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "...",
      "agent_response": "...",
      "agent_tools": [],
      "scores": {"correctness": 0-10, "tool_selection": 0-10, "context_retention": 0-10, "completeness": 0-10, "efficiency": 0-10, "personality": 0-10, "error_recovery": 0-10},
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "short explanation"
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
- The critical test is NO TOOL CALLS for any of the 3 turns
- If agent uses any document/file/search tool, that is an over-tooling failure
