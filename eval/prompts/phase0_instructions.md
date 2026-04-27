# Phase 0 Eval Instructions — Product Comparison Scenario

You are the GAIA Eval Agent. Execute this eval scenario using the gaia-agent-ui MCP tools available to you.

## GROUND TRUTH
File: C:\Users\14255\Work\gaia4\eval\corpus\documents\product_comparison.html

Known facts:
- Product names: StreamLine ($49/month) vs ProFlow ($79/month)
- Price difference: $30/month (ProFlow costs more)
- StreamLine: 10 integrations. ProFlow: 25 integrations
- StreamLine: 4.2 stars. ProFlow: 4.7 stars

## STEPS

### 1. Verify infrastructure
Call mcp__gaia-agent-ui__system_status — confirm lemonade_running=true and model_loaded is set.
If not running, write INFRA_ERROR to results and stop.

### 2. Create session
Call mcp__gaia-agent-ui__create_session with title "Eval: Phase 0 Product Comparison"
Record the session_id from the response.

### 3. Index document
Call mcp__gaia-agent-ui__index_document with:
  path = "C:\\Users\\14255\\Work\\gaia4\\eval\\corpus\\documents\\product_comparison.html"
Record chunk_count from the response.
If chunk_count = 0 or error, write SETUP_ERROR to results and stop.

### 4. Turn 1
Call mcp__gaia-agent-ui__send_message with:
  session_id = <from step 2>
  message = "What products are being compared in this document and how do their prices differ?"
Record the full content response and list of tools used.
Judge: Did agent mention $49, $79, and $30 difference? Score correctness 0-10.

### 5. Turn 2
Call mcp__gaia-agent-ui__send_message with:
  session_id = <same>
  message = "Which product has more integrations and by how much?"
Record response.
Judge: Did agent say ProFlow has 25 vs StreamLine's 10 (15 more)? Score correctness 0-10.

### 6. Turn 3
Call mcp__gaia-agent-ui__send_message with:
  session_id = <same>
  message = "What about the star ratings for each product?"
Record response.
Judge: Did agent get StreamLine=4.2 and ProFlow=4.7? Score correctness 0-10.

### 7. Get full traces
Call mcp__gaia-agent-ui__get_messages with the session_id.
Note any agent_steps visible in the response.

### 8. Write results
Write C:\Users\14255\Work\gaia4\eval\results\phase0\result.json with:
{
  "scenario_id": "phase0_product_comparison",
  "status": "PASS or FAIL",
  "overall_score": <average of turn correctness scores>,
  "session_id": "<session_id>",
  "chunk_count": <from step 3>,
  "turns": [
    {
      "turn": 1,
      "user_message": "...",
      "agent_response": "...",
      "agent_tools": ["tools used"],
      "scores": {"correctness": 0-10},
      "pass": true/false,
      "reasoning": "brief explanation"
    },
    ... (turns 2 and 3 same structure)
  ],
  "root_cause": null or "description of failures",
  "timestamp": "<ISO 8601 timestamp>"
}

Write C:\Users\14255\Work\gaia4\eval\results\phase0\summary.md with a readable summary.

## PASS CRITERIA
- PASS if overall_score >= 6.0 (loop ran end-to-end, agent mostly correct)
- FAIL if overall_score < 6.0 (agent gave wrong answers)
- SETUP_ERROR if indexing failed
- INFRA_ERROR if GAIA not running

## IMPORTANT
- Do NOT delete sessions or files
- Use absolute Windows paths (C:\...) for all file operations
- Be honest with scores — this validates the eval loop
