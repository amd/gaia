# Scenario-Level Judge Instructions

After all turns are complete, evaluate the scenario holistically to populate the
`root_cause` and `recommended_fix` fields in the final result, and to confirm or
override the `status` field derived from per-turn scores.

## Questions to answer

1. Did the agent complete the overall task across all turns?
2. Was the conversation coherent (context carried forward correctly)?
3. If any turns failed, what is the single root cause?
4. What specific code change would fix the failure?

## Status confirmation

Review the per-turn pass/fail results and set `status` in the final result as follows
(first matching rule wins):

- **BLOCKED_BY_ARCHITECTURE**: The agent demonstrably could not succeed due to a known
  architectural constraint (e.g. history truncation, stateless agent, tool results not
  in history). Use this only when the architecture audit confirms the blocker.
- **PASS**: All turns passed (or failures were in non-critical turns and overall task
  was completed). A "non-critical turn" is one whose failure does not prevent the user's
  primary goal from being achieved — e.g., a preamble turn that asks what docs are loaded,
  where the substantive Q&A turns all passed. A turn that contains the scenario's primary
  factual query is always critical.
- **FAIL**: One or more turns failed and the failure is attributable to agent behavior
  (wrong answer, hallucination, lazy refusal, etc.).

## Root cause categories

- `architecture`: Requires changes to `_chat_helpers.py`, agent persistence, or history
- `prompt`: Requires changes to the system prompt in `agent.py`
- `tool_description`: Requires updating tool docstrings
- `rag_pipeline`: Requires changes to how documents are indexed or retrieved

## Fields to populate in the final result JSON

Set these two fields at the top level of the result object returned in Phase 6:

- **`root_cause`**: `null` if all turns passed, otherwise a 1-2 sentence description
  of the failure root cause (e.g. `"Agent did not retain file path from turn 1 because
  tool results are excluded from history."`).

- **`recommended_fix`**: `null` if all turns passed, otherwise:
  ```json
  {
    "target": "architecture|prompt|tool_description|rag_pipeline",
    "file": "path/to/file.py",
    "description": "specific change to make"
  }
  ```

Do **not** add a `scenario_complete` field — it is not part of the result schema.
