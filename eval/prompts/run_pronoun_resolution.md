# Eval Scenario: pronoun_resolution

Run this eval scenario against the live GAIA Agent UI via MCP tools.
Write results to: `C:\Users\14255\Work\gaia4\eval\results\phase2\pronoun_resolution.json`

## Ground Truth
File: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`

Known facts:
- Turn 1: PTO for first-year employees = **15 days** (Section 4, accrual table)
- Turn 2: Remote work = **up to 3 days per week with manager approval** (Section 7). Fully remote requires VP approval.
- Turn 3: Contractors NOT eligible — **benefits are for full-time employees only** (Sections 3 and 5). CRITICAL: agent must NOT say contractors are eligible.

## Critical Test
Turn 3 is the key test. The agent must correctly state that contractors are NOT eligible.
The agent MUST NOT:
- Say contractors are eligible for the remote work policy
- Confuse contractor eligibility with employee policies
- Fail to resolve "that policy" as referring to the remote work policy discussed in Turn 2

The agent MUST:
- Understand "that policy" refers to the remote work policy from Turn 2
- State that contractors are NOT covered (they use service agreements, not the employee handbook)

## Steps

1. Call `system_status()` — verify Agent UI is running. If error, abort and write status="INFRA_ERROR".

2. Call `create_session("Eval: pronoun_resolution")`

3. Call `index_document` with path: `C:\Users\14255\Work\gaia4\eval\corpus\documents\employee_handbook.md`
   - Check chunk_count > 0. If 0 or error → write status="SETUP_ERROR" and stop.

4. **Turn 1** — Call `send_message(session_id, "What is the PTO policy for new employees?")`
   - Expected: Agent states "15 days" for first-year employees
   - Score all 7 dimensions
   - context_retention = 10 automatically (first turn)

5. **Turn 2 (pronoun test)** — Call `send_message(session_id, "What about remote work — does it have a policy too?")`
   - Note: "it" is ambiguous — agent must resolve it as referring to the employee handbook/company policies
   - Expected: Agent states employees may work remotely up to 3 days/week with manager approval
   - Expected bonus: mention VP approval for fully remote
   - FAIL if agent asks for clarification without attempting to answer
   - Score context_retention highly if agent correctly interprets "it" without re-asking what doc to check

6. **Turn 3 (critical contractor test)** — Call `send_message(session_id, "Does that policy apply to contractors too?")`
   - Note: "that policy" refers to the remote work policy from Turn 2
   - Expected: Agent states NO — contractors are NOT eligible; benefits and policies are for full-time employees only
   - CRITICAL FAIL if agent says contractors ARE eligible
   - CRITICAL FAIL if agent fails to resolve "that policy" and asks what policy the user means
   - Score correctness=10 if agent clearly states contractors NOT eligible
   - Score correctness=0 if agent says contractors are eligible

7. Call `get_messages(session_id)` to capture full trace.

8. Call `delete_session(session_id)` to clean up.

9. Write result JSON to `C:\Users\14255\Work\gaia4\eval\results\phase2\pronoun_resolution.json`

## Scoring Rules
- overall_score = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05
- PASS if overall_score >= 6.0 AND no critical failure
- Turn 1: context_retention = 10 automatically (first turn, N/A)
- Turn 2: context_retention = how well agent resolved "it" as referring to handbook policies
- Turn 3: context_retention = how well agent resolved "that policy" as remote work policy from Turn 2
- CRITICAL FAIL: agent says contractors ARE eligible for any policy (correctness=0, status=FAIL)
- CRITICAL FAIL: agent fails to attempt resolution of pronoun (asks user to clarify rather than using context)

## Result JSON format
```json
{
  "scenario_id": "pronoun_resolution",
  "status": "PASS or FAIL",
  "overall_score": 0-10,
  "turns": [
    {
      "turn": 1,
      "user_message": "What is the PTO policy for new employees?",
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
- The `eval/results/phase2/` directory already exists
- Be honest: contractor eligibility error is a CRITICAL failure (correctness=0, status=FAIL)
- Pronoun resolution failure (asking for clarification rather than resolving) scores context_retention=2
