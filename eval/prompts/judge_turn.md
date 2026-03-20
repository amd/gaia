# Per-Turn Judge Instructions

After each agent response, evaluate:

1. Did the agent correctly answer the question? Compare to ground truth if provided.
2. Did the agent use the right tools? Were there unnecessary calls?
3. Did the agent use information from previous turns?
4. Was the answer complete?
5. Was the path to the answer efficient?
6. Did the agent sound natural (not sycophantic, not overly verbose)?
7. If any tool failed, did the agent recover gracefully?

Score each dimension 0-10 per the weights in simulator.md.

Output format:
{
  "scores": {
    "correctness": N,
    "tool_selection": N,
    "context_retention": N,
    "completeness": N,
    "efficiency": N,
    "personality": N,
    "error_recovery": N
  },
  "overall_score": N.N,
  "pass": true/false,
  "failure_category": null or "category_name",
  "reasoning": "1-2 sentence explanation"
}
