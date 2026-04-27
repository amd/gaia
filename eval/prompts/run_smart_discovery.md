# Eval Scenario: smart_discovery

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase2\smart_discovery.json`

## Ground Truth
File to discover: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`

Known facts (in that file):
- Turn 1: PTO for first-year employees = **15 days** (Section 4)
- Turn 2: Remote work = **up to 3 days per week with manager approval** (Section 7)

## Critical Test
**NO documents are pre-indexed for this session.** The agent must:
1. Recognize it needs to find a relevant document
2. Use browse_files or search_files to discover `employee_handbook.md` in the corpus directory
3. Index it using index_document
4. Answer correctly from the indexed content

The agent MUST NOT:
- Say "I don't have any documents available" without trying to find them
- Make up an answer without indexing a document
- Ask the user "can you provide a document?"

The agent MUST:
- Proactively search for relevant files
- Find and index employee_handbook.md (or similar HR/policy document)
- Answer the PTO question correctly

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: smart_discovery")`
   - **DO NOT call index_document** — this session starts with NO indexed documents
   - This is the critical difference from other scenarios

3. **Turn 1 (smart discovery test)** — Call `send_message(session_id, "What is the PTO policy for new employees?")`
   - Expected flow: Agent searches for HR/policy documents → finds employee_handbook.md → indexes it → answers "15 days"
   - CRITICAL FAIL if agent says "I don't have any documents" without trying to search
   - CRITICAL FAIL if agent makes up an answer without indexing a document
   - PASS if agent discovers and indexes employee_handbook.md and correctly states 15 days
   - Score tool_selection based on whether agent used appropriate discovery tools (browse_files, search_files, index_document)
   - Score correctness=10 if final answer states 15 days, correctness=0 if agent gives up or hallucinates

4. **Turn 2 (already-indexed recall)** — Call `send_message(session_id, "What about the remote work policy?")`
   - Expected: Agent answers from already-indexed employee_handbook.md WITHOUT re-indexing
   - Expected answer: up to 3 days per week with manager approval
   - Score efficiency highly if agent answers without re-indexing (uses cached/indexed content)
   - Deduct efficiency if agent re-indexes the same document it already indexed in Turn 1

5. Call `get_messages(session_id)` to capture full trace.

6. Call `delete_session(session_id)` to clean up.

7. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase2\smart_discovery.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- Turn 1: context_retention = 10 automatically (first turn)
- Turn 2: context_retention = did agent recall the document indexed in Turn 1?
- CRITICAL FAIL: Turn 1 agent says "no documents available" without attempting discovery
- CRITICAL FAIL: Turn 1 agent answers without using a document (hallucination)
- Partial credit: if agent searched but found wrong file or indexed wrong document, score correctness=4

## Corpus directory for discovery
The corpus documents are located at:
`C:\Users\14255\Work\gaia4\eval\corpus\documents\`

Files available in corpus:
- product_comparison.html
- employee_handbook.md  ← the target
- acme_q3_report.md
- meeting_notes_q3.txt
- api_reference.py
- sales_data_2025.csv
- large_report.md
- budget_2025.md
- empty.txt
- unicode_test.txt
- duplicate_sections.md

The agent should ideally find `employee_handbook.md` for an HR policy question. If it indexes a different document (e.g., meeting notes) and can't answer, that's also a valid test of error recovery.

## Result JSON format
```json
{
  "scenario_id": "smart_discovery",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "What is the PTO policy for new employees?",
      "agent_response": "...",
      "agent_tools": ["browse_files", "index_document", "query_specific_file"],
      "scores": {"correctness": 0-10, "tool_selection": 0-10, "context_retention": 0-10, "completeness": 0-10, "efficiency": 0-10, "personality": 0-10, "error_recovery": 0-10},
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "short explanation"
    }
  ],
  "root_cause": null,
  "recommended_fix": null,
  "cost_estimate": {"turns": 2, "estimated_usd": 0.03}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase2/` directory already exists
- DO NOT pre-index any document — the session must start empty
- Be honest: if agent gives up without searching, that's a CRITICAL FAIL (correctness=0, status=FAIL)
- The discovery behavior is the entire point of this test
