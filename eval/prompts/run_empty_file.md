# Eval Scenario: empty_file

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\empty_file.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\empty.txt`
- This file is intentionally empty (0 bytes or just whitespace)
- Expected agent behavior: reports file is empty, does NOT crash, does NOT hallucinate content

## Scenario Purpose
Test the agent's behavior when asked to index and query an **empty file**.
The agent must handle this edge case gracefully without crashing or making up content.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: empty_file")`

3. **Turn 1 (index and query empty file)** — Call `send_message(session_id, "Please read C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\empty.txt and tell me what's in it.")`
   - Expected: Agent attempts to index the file, gets chunk_count=0, then reports to the user that the file is empty
   - CRITICAL FAIL if agent fabricates content for the empty file
   - CRITICAL FAIL if agent crashes with an unhandled exception visible to the user
   - PASS if agent clearly states the file is empty or has no content
   - Score correctness=10 if agent says file is empty (or similar)
   - Score error_recovery=10 if agent handles gracefully without crash/trace

4. **Turn 2 (follow-up on empty file)** — Call `send_message(session_id, "Are there any action items in that file?")`
   - Expected: Agent states there is no content, so no action items can be found
   - CRITICAL FAIL if agent makes up action items from an empty file
   - Score correctness=10 if agent clearly states no action items (file is empty)
   - Score context_retention=10 if agent remembers from Turn 1 that the file is empty

5. **Turn 3 (recover with valid file)** — Call `send_message(session_id, "OK, can you instead summarize C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\meeting_notes_q3.txt?")`
   - This file EXISTS and has real content
   - Expected: Agent successfully indexes and summarizes meeting_notes_q3.txt
   - Score error_recovery=10 if agent successfully pivots from the empty file to a valid one
   - Score correctness=10 if summary includes meeting-related content (date, action items, decisions)

6. Call `get_messages(session_id)` to capture full trace.

7. Call `delete_session(session_id)` to clean up.

8. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\empty_file.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 1: agent fabricates content for empty file
- CRITICAL FAIL Turn 2: agent fabricates action items from empty file
- CRITICAL FAIL: agent exposes raw exception or stack trace to user

## Result JSON format
```json
{
  "scenario_id": "empty_file",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "chunk_count_empty_file": 0,
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
  "cost_estimate": {"turns": 3, "estimated_usd": 0.03}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase3/` directory already exists
- empty.txt is at `C:\Users\14255\Work\gaia4\eval\corpus\documents\empty.txt`
- The file IS intentionally empty — do not check if this is wrong
- CRITICAL: do NOT fabricate content for the empty file
