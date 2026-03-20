# Eval Scenario: cross_section_rag

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\cross_section_rag.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`

Known facts (distributed across multiple sections):
- Q3 revenue: $14.2 million (Section: Revenue Summary)
- YoY growth: 23% (compared to Q3 2024's $11.5M)
- Q4 CEO outlook: "15-18% growth driven by enterprise segment expansion" (Section: CEO Letter)
- Key driver: enterprise segment expansion

## Scenario Purpose
Test whether the agent can **synthesize facts from multiple sections** of a single document.
- Turn 1: Ask a question requiring facts from 2+ sections to answer fully
- Turn 2: Follow-up requiring the agent to connect the Q4 projection to the Q3 baseline
- Turn 3: Probe for a specific section quote

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: cross_section_rag")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

4. **Turn 1 (cross-section synthesis)** — Call `send_message(session_id, "Give me a complete financial summary of Acme Corp's Q3 performance and what to expect in Q4.")`
   - Expected: Agent answers with BOTH Q3 revenue ($14.2M, 23% YoY) AND Q4 outlook (15-18% growth, enterprise segment)
   - CRITICAL FAIL if agent only gives Q3 revenue without Q4 outlook (or vice versa)
   - Score correctness=10 if both "$14.2 million" (or "$14.2M") AND "15-18%" are present in response
   - Score correctness=5 if only one section answered
   - Score correctness=0 if both are missing or hallucinated

5. **Turn 2 (cross-reference)** — Call `send_message(session_id, "If Q4 hits the low end of that projection, what would be the full-year 2025 revenue?")`
   - Expected: Agent calculates: Q3 baseline $14.2M → Q4 at 15% growth = $14.2M * 1.15 ≈ $16.3M
   - Full year estimate requires knowing Q3 revenue AND Q4 growth rate — cross-section synthesis + reasoning
   - PASS if agent acknowledges needing Q1/Q2 data for true full-year total, OR attempts reasonable calculation
   - CRITICAL FAIL if agent makes up a number without showing reasoning
   - Score correctness=8 if agent correctly identifies what data is needed but says it doesn't have Q1/Q2
   - Score correctness=10 if agent calculates Q4 projection correctly from stated Q3 figures

6. **Turn 3 (quote retrieval)** — Call `send_message(session_id, "What exact words did the CEO use about Q4?")`
   - Expected: Agent retrieves the CEO letter section and quotes it
   - Expected quote contains: "15-18% growth" and "enterprise segment"
   - Score correctness=10 if quoted text contains both "15-18%" and "enterprise"
   - CRITICAL FAIL if agent fabricates a CEO quote not in the document

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\cross_section_rag.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL Turn 1: response contains neither Q3 revenue nor Q4 outlook
- CRITICAL FAIL Turn 3: agent fabricates a CEO quote

## Result JSON format
```json
{
  "scenario_id": "cross_section_rag",
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
- Turn 1 is the critical cross-section synthesis test: BOTH Q3 revenue AND Q4 outlook must appear
- Turn 3: if the document doesn't contain an exact CEO quote, score correctness=7 if agent paraphrases correctly without fabrication
