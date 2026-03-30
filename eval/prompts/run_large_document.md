# Eval Scenario: large_document

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\large_document.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\large_report.md`
- Size: ~19,193 words, 75 sections
- Buried fact at ~65% depth (around Section 48-52):
  "Three minor non-conformities in supply chain documentation" — this is the compliance finding
- The fact is buried deep in the document and requires chunked retrieval to find

## Scenario Purpose
Test whether the agent can **retrieve a deeply buried fact** from a large document
that has been chunked into many RAG chunks. This tests chunk coverage and retrieval
quality at depth — not just retrieval of content near the beginning of the document.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: large_document")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\large_report.md`
   - Note the chunk_count — this should be a large number (20+ chunks for a 19K word doc)
   - If chunk_count=0 → write status="SETUP_ERROR" and stop.
   - If chunk_count < 5 → note as a potential coverage issue but continue

4. **Turn 1 (deep retrieval)** — Call `send_message(session_id, "What was the compliance finding mentioned in the report around section 50?")`
   - Expected: Agent retrieves "Three minor non-conformities in supply chain documentation"
   - This tests whether RAG can retrieve content from ~65% depth in a 19K-word document
   - CRITICAL FAIL if agent fabricates a compliance finding not in the document
   - Score correctness=10 if response contains "three minor non-conformities" and "supply chain"
   - Score correctness=5 if agent finds a compliance finding but with wrong details
   - Score correctness=0 if agent makes up something entirely different
   - Score error_recovery=8 if agent says it can't find section 50 specifically but searches broadly

5. **Turn 2 (early-section fact for comparison)** — Call `send_message(session_id, "What is the main topic or title of this report?")`
   - Expected: Agent can answer easily from early chunks (Section 1)
   - Tests whether easy early-document retrieval works (baseline comparison)
   - Score correctness=10 if agent provides a relevant title/topic from the report

6. **Turn 3 (another deep fact)** — Call `send_message(session_id, "What recommendations were made regarding the supply chain issues?")`
   - Tests whether agent can retrieve related content near the buried compliance finding
   - CRITICAL FAIL if agent fabricates recommendations not in the document
   - Score correctness=10 if response is grounded in the actual document content
   - If document doesn't have recommendations section, score correctness=8 if agent honestly says it couldn't find specific recommendations

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\large_document.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Agent fabricates compliance findings not in the document
- Note: Record the chunk_count from index_document — this is key diagnostic data

## Result JSON format
```json
{
  "scenario_id": "large_document",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "chunk_count": 0,
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
- Record chunk_count from index_document — this is critical diagnostic data
- Ground truth: "Three minor non-conformities in supply chain documentation"
- If chunk_count is very low (< 5), note this as a coverage concern in root_cause
