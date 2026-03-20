# Scenario-Level Judge Instructions

After all turns are complete, evaluate the scenario holistically:

1. Did the agent complete the overall task?
2. Was the conversation coherent across turns?
3. What is the root cause of any failures?
4. What specific code change would fix the issue?

Categories:
- architecture: Requires changes to _chat_helpers.py, agent persistence, history
- prompt: Requires changes to system prompt in agent.py
- tool_description: Requires updating tool docstrings
- rag_pipeline: Requires changes to how documents are indexed or retrieved

Output format:
{
  "scenario_complete": true/false,
  "root_cause": null or "description",
  "recommended_fix": null or {
    "target": "architecture|prompt|tool_description|rag_pipeline",
    "file": "path/to/file.py",
    "description": "specific change to make"
  }
}
