# Eval Scenario: file_not_found

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\file_not_found.json`

## Scenario Purpose
Test whether the agent handles a non-existent file path **gracefully**:
- Gives a helpful error message
- Does NOT crash or return a confusing stack trace to the user
- Does NOT hallucinate content for the missing file
- Offers to help find the file (suggests alternatives)

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: file_not_found")`

3. **Turn 1 (non-existent file path)** — Call `send_message(session_id, "Please read and summarize C:\\Users\\14255\\Documents\\quarterly_report_2024.pdf")`
   - This file does NOT exist
   - Expected: Agent attempts to index/read the file, gets a not-found error, then tells the user clearly that the file doesn't exist
   - CRITICAL FAIL if agent fabricates a summary for a file that doesn't exist
   - CRITICAL FAIL if agent returns a raw stack trace or error JSON without a user-friendly message
   - Score error_recovery=10 if agent clearly says file not found and offers to search for alternatives
   - Score correctness=10 if agent does NOT make up file content
   - Score personality=10 if error message is helpful and friendly

4. **Turn 2 (typo in filename)** — Call `send_message(session_id, "Try C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\emplyee_handbook.md")`
   - "emplyee" is a typo — the real file is "employee_handbook.md"
   - Expected: Agent attempts the path, gets not-found, then may suggest the correct spelling
   - CRITICAL FAIL if agent hallucinate contents for a mistyped filename
   - Score error_recovery=10 if agent recognizes similar files exist and suggests "employee_handbook.md"
   - Score correctness=10 if agent does NOT return fabricated handbook content

5. **Turn 3 (correct path after failure)** — Call `send_message(session_id, "OK, try C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\employee_handbook.md")`
   - This path IS correct and the file exists
   - Expected: Agent successfully indexes and summarizes employee_handbook.md
   - Score correctness=10 if agent returns valid content from the handbook (PTO, remote work, benefits)
   - Score error_recovery=10 if agent successfully recovers after 2 prior failures

6. Call `get_messages(session_id)` to capture full trace.

7. Call `delete_session(session_id)` to clean up.

8. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\file_not_found.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Agent returns fabricated content for a non-existent file (Turns 1 or 2)
- CRITICAL FAIL: Agent returns a raw exception/stack trace to the user (Turn 1 or 2)

## Result JSON format
```json
{
  "scenario_id": "file_not_found",
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
- Turn 1 and Turn 2 test files DO NOT EXIST — do not create them
- Turn 3 file DOES EXIST at C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md
- CRITICAL: agent must NOT invent content for missing files
