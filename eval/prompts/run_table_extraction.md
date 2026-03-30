# Eval Scenario: table_extraction

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\table_extraction.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\sales_data_2025.csv`

Known facts:
- Best-selling product in March 2025: Widget Pro X (142 units, $28,400 revenue)
- Q1 2025 total revenue: $342,150
- Top salesperson by revenue: Sarah Chen with $70,000

## Scenario Purpose
Test whether the agent can answer questions about **tabular / CSV data** by correctly extracting and aggregating values from sales_data_2025.csv.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: table_extraction")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\sales_data_2025.csv`
   - Check chunk_count > 0. If 0 → write status="SETUP_ERROR" and stop.

4. **Turn 1 (aggregation from CSV)** — Call `send_message(session_id, "What was the best-selling product in March 2025 by revenue?")`
   - Expected: "Widget Pro X" with revenue $28,400 and 142 units
   - CRITICAL FAIL if agent says it cannot answer questions about CSV data without trying
   - Score correctness=10 if response mentions "Widget Pro X" and "$28,400" (or "28400")
   - Score correctness=5 if agent identifies the right product but wrong revenue
   - Score correctness=0 if completely wrong product or hallucinated

5. **Turn 2 (summation)** — Call `send_message(session_id, "What was the total Q1 2025 revenue across all products?")`
   - Expected: $342,150
   - Score correctness=10 if response mentions "$342,150" or "342,150"
   - Score correctness=5 if agent gives a plausible but incorrect total with reasoning
   - Note: The agent may not be able to sum 500 rows from RAG chunks — if it acknowledges this limitation honestly, score error_recovery=8

6. **Turn 3 (top-N lookup)** — Call `send_message(session_id, "Who was the top salesperson by total revenue in Q1?")`
   - Expected: Sarah Chen with $70,000
   - Score correctness=10 if response mentions "Sarah Chen" and approximately "$70,000"
   - Score correctness=5 if right name, wrong revenue amount
   - Score error_recovery=8 if agent honestly says it cannot aggregate 500 rows but attempts to answer

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\table_extraction.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Agent claims it cannot process CSV data at all without attempting a query
- Note: CSV aggregation is hard for RAG — partial credit if agent gets directionally correct answers or honestly acknowledges the limitation

## Result JSON format
```json
{
  "scenario_id": "table_extraction",
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
- CSV RAG is inherently challenging — the index may only contain a sample of rows, not all 500
- Be fair: if the agent answers honestly about limitations, that is better than hallucinating exact totals
- Ground truth: Widget Pro X (142 units, $28,400), total Q1 = $342,150, top salesperson = Sarah Chen ($70,000)
