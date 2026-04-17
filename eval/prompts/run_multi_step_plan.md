# Eval Scenario: multi_step_plan

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase3\multi_step_plan.json`

## Ground Truth
Files needed:
- `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
- `C:\Users\14255\Work\gaia4\eval\corpus\documents\sales_data_2025.csv`

## Scenario Purpose
Test whether the agent can handle a **complex multi-tool request** that requires:
1. Indexing multiple documents
2. Querying them in sequence
3. Synthesizing results into a coherent answer
The agent must plan and execute multiple steps without getting lost.

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: multi_step_plan")`
   - Do NOT pre-index any documents

3. **Turn 1 (complex multi-document request)** — Call `send_message(session_id, "I need you to: 1) Find and index both the Acme Q3 report and the sales data CSV from the eval corpus, 2) Tell me the Q3 revenue from the report, and 3) Tell me the top product from the sales data.")`
   - Expected: Agent understands this is a 3-step task, indexes both files, answers both questions
   - Expected answers: Q3 revenue = $14.2 million; Top product = Widget Pro X
   - Score tool_selection=10 if agent correctly indexes both files AND queries both
   - Score completeness=10 if agent answers BOTH questions (revenue AND top product)
   - Score tool_selection=5 if agent only indexes/answers one of the two
   - CRITICAL FAIL if agent makes up answers without indexing the files
   - Note: sales CSV has only 2 chunks — partial credit if agent notes it can only see a sample

4. **Turn 2 (follow-up synthesis)** — Call `send_message(session_id, "Based on what you found, which document is more useful for understanding the company's overall Q1 2025 performance?")`
   - Expected: Agent synthesizes across both docs to give a reasoned answer
   - Q3 report gives high-level summaries; sales CSV gives transaction details (if chunked properly)
   - Score correctness=8 if agent gives a reasoned answer grounded in what it found in Turn 1
   - Score context_retention=10 if agent recalls which docs it indexed in Turn 1

5. Call `get_messages(session_id)` to capture full trace.

6. Call `delete_session(session_id)` to clean up.

7. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase3\multi_step_plan.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- CRITICAL FAIL: Agent makes up answers without indexing files
- Note: Widget Pro X may not appear in 2 CSV chunks — partial credit if agent honestly says it can only see a sample

## Corpus paths (eval task must use these exact paths):
- `C:\Users\14255\Work\gaia4\eval\corpus\documents\acme_q3_report.md`
- `C:\Users\14255\Work\gaia4\eval\corpus\documents\sales_data_2025.csv`

## Result JSON format
```json
{
  "scenario_id": "multi_step_plan",
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
  "cost_estimate": {"turns": 2, "estimated_usd": 0.04}
}
```

## IMPORTANT
- Use absolute Windows paths with backslashes for all file operations
- The `eval/results/phase3/` directory already exists
- Agent must discover and index files from the corpus path (not pre-indexed)
- Ground truth: Q3 revenue=$14.2M, top product=Widget Pro X
