# Eval Scenario: known_path_read

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\known_path_read.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\meeting_notes_q3.txt`

Known facts:
- Next meeting: October 15, 2025 at 2:00 PM
- This is a plain text file with meeting notes

## Scenario Purpose
Test whether the agent uses the RIGHT tool when the user provides an exact file path.
- The user gives the full path explicitly → agent should use index_document (to index it) then query_specific_file
- Agent should NOT call query_documents with a keyword search when the path is explicitly given
- Agent should NOT ask "can you provide the file path?" when the user just gave it

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: known_path_read")`
   - Do NOT pre-index any documents — session starts empty

3. **Turn 1 (explicit path provided)** — Call `send_message(session_id, "Please read the file at C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\meeting_notes_q3.txt and tell me when the next meeting is.")`
   - Expected flow: Agent indexes the file → queries it → answers "October 15, 2025 at 2:00 PM"
   - CRITICAL FAIL if agent says "I can't access that file" without attempting to index it
   - CRITICAL FAIL if agent uses query_documents keyword search instead of indexing the specific file
   - Score tool_selection=10 if agent calls index_document with the given path then queries it
   - Score tool_selection=5 if agent uses query_documents but still answers correctly somehow
   - Score tool_selection=0 if agent refuses to try or uses completely wrong approach

4. **Turn 2 (follow-up about same file)** — Call `send_message(session_id, "What were the action items discussed in that meeting?")`
   - Expected: Agent queries the already-indexed meeting_notes_q3.txt without re-indexing
   - Score efficiency=10 if agent answers without re-indexing (file already in session)
   - Score efficiency=5 if agent re-indexes unnecessarily but answers correctly
   - Score context_retention=10 if agent correctly recalls which file "that meeting" refers to

5. **Turn 3 (different file by path)** — Call `send_message(session_id, "Now read C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\acme_q3_report.md and tell me the Q3 revenue.")`
   - Expected: Agent indexes the new file → queries it → answers "$14.2 million"
   - This tests whether agent can switch to a new file when user provides a different path
   - Score tool_selection=10 if agent indexes new file and answers correctly
   - CRITICAL FAIL if agent answers with meeting notes data instead of financial data

6. Call `get_messages(session_id)` to capture full trace.

7. Call `delete_session(session_id)` to clean up.

8. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\known_path_read.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Agent says "I can't access files" without attempting index_document
- CRITICAL FAIL: Agent mixes up meeting_notes and acme_q3_report content

## Result JSON format
```json
{
  "scenario_id": "known_path_read",
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
- Key test: agent must use index_document when given explicit path, not keyword search
- The meeting date "October 15, 2025 at 2:00 PM" is the ground truth for Turn 1
