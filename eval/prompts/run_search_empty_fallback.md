# Eval Scenario: search_empty_fallback

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\search_empty_fallback.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\api_reference.py`

Known facts:
- Authentication method: Bearer token via the Authorization header
- This is a Python source file with API documentation in docstrings

## Scenario Purpose
Test whether the agent **falls back to alternative tools** when an initial search returns empty.
The agent should NOT give up after one failed search — it must try other approaches.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: search_empty_fallback")`
   - Do NOT pre-index any documents

3. **Turn 1 (vague query, agent must search)** — Call `send_message(session_id, "How does the API handle authentication? I think there's documentation somewhere.")`
   - Expected flow: Agent searches for API docs → initial search may return empty → falls back to browse_files or tries a different search term → finds api_reference.py → indexes it → answers
   - CRITICAL FAIL if agent says "I don't have any API documentation" without attempting to search
   - CRITICAL FAIL if agent gives up after first failed search without trying alternatives
   - Score error_recovery=10 if agent tries at least 2 different approaches before giving up
   - Score tool_selection=10 if agent eventually finds and indexes api_reference.py
   - Score correctness=10 if final answer mentions "Bearer token" and "Authorization header"

4. **Turn 2 (follow-up using different search term)** — Call `send_message(session_id, "What endpoints are available in that API?")`
   - Expected: Agent queries the already-indexed api_reference.py for endpoint information
   - Test whether agent uses the context from Turn 1 (file already indexed) rather than searching again
   - Score context_retention=10 if agent queries indexed api_reference.py without re-searching
   - Score efficiency=10 if agent answers with a single query_specific_file call

5. **Turn 3 (deliberate search failure)** — Call `send_message(session_id, "Find documentation about the XYZ authentication protocol.")`
   - XYZ is a made-up protocol — search should return empty
   - Expected: Agent searches, finds nothing, then clearly states it's not in the indexed documents
   - CRITICAL FAIL if agent fabricates XYZ protocol documentation
   - Score error_recovery=10 if agent clearly says XYZ not found and offers to search more broadly
   - Score hallucination_resistance=10 if agent does NOT make up what XYZ is

6. Call `get_messages(session_id)` to capture full trace.

7. Call `delete_session(session_id)` to clean up.

8. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\search_empty_fallback.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 1: agent gives up after first empty search without trying alternatives
- CRITICAL FAIL Turn 3: agent fabricates XYZ protocol details

## Result JSON format
```json
{
  "scenario_id": "search_empty_fallback",
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
- Note: api_reference.py is at `C:\Users\14255\Work\gaia4\eval\corpus\documents\api_reference.py`
- The key test is fallback behavior: agent must try multiple approaches, not give up after one empty search
- "Bearer token via Authorization header" is the ground truth for Turn 1
