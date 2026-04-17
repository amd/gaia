# Per-Turn Judge Instructions

After each agent response, evaluate using the rules below.

## STEP 1 — PRE-CHECK (mandatory, apply before scoring)

If ANY check fails: set correctness=0, completeness=0, tool_selection=0, failure_category as noted.

1. **Garbled output**: Response is mostly `}`, `{`, brackets, or <3 readable English words → failure_category=garbled_output
2. **Raw JSON leak**: Response is a JSON blob with keys like `"chunks"`, `"scores"`, `"tool"`, `"result"` → failure_category=garbled_output
3. **Non-answer**: Response is completely off-topic or empty → failure_category=wrong_answer
4. **Tool-call artifact**: Response is ONLY a `[tool:X]` label, not prose → failure_category=garbled_output

## STEP 2 — AUTOMATIC ZERO RULES (apply only when `expected_answer` is a non-null string)

If the turn uses `expected_behavior` instead of `expected_answer`, skip these rules and score correctness behaviorally (10=exact match, 7=mostly correct, 4=partial, 0=wrong behavior).

If `expected_answer` is `null`, the ground truth asserts that NO specific answer exists in the document. The agent should indicate the information is not available. Saying "I don't know" or "the document doesn't mention this" = correctness up to 10. Inventing a specific answer = correctness=0. The lazy-refusal auto-zero rule does NOT apply for null expected_answer turns.

When `expected_answer` IS a non-null string, these force correctness=0:
- Wrong number: ground_truth has a number and response is >5% off
- Wrong name: ground_truth names a person/entity and response names a different one
- Lazy refusal: agent says "can't find" without calling a query tool first
- Hallucinated source: agent claims a fact "from the document" that contradicts ground_truth

## STEP 3 — SCORE EACH DIMENSION (0-10)

- **correctness** (25%): Factual accuracy vs ground_truth. 10=exact, 7=minor omissions, 4=partial, 0=wrong/hallucinated
- **tool_selection** (20%): Right tools in right order. 10=optimal, 7=correct+extra calls, 4=wrong tool but recovered, 0=wrong/missing
- **context_retention** (20%): Used prior-turn info. 10=perfect, 7=mostly, 4=missed key info, 0=ignored. Cap at 4 if agent re-asks established info. If prior turn failed, judge against ground_truth not the failed response.
- **completeness** (15%): Fully answered all parts. 10=complete, 7=mostly, 4=partial, 0=didn't answer
- **efficiency** (10%): Steps vs optimal. 10=optimal, 7=1-2 extra, 4=many redundant, 0=tool loop (3+ identical calls)
- **personality** (5%): Direct and confident, no sycophancy. 10=concise+direct, 7=neutral/functional, 4=generic AI hedging, 0=sycophantic
- **error_recovery** (5%): Handles tool failures gracefully. 10=graceful, 7=recovered after retry, 4=partial, 0=gave up

## STEP 4 — OVERALL SCORE AND PASS/FAIL

overall = correctness*0.25 + tool_selection*0.20 + context_retention*0.20 + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05

Pass/fail decision (apply in order):
1. FAIL if correctness=0
2. FAIL if correctness < 4
3. FAIL if overall_score < 6.0
4. PASS otherwise

## STEP 5 — RECORD PERFORMANCE DATA

After scoring, capture inference performance stats from the `send_message()` response.
These metrics do NOT affect the pass/fail decision — they are recorded for reporting.

Check the `send_message()` return value for a `stats` key containing:
- `tokens_per_second`, `time_to_first_token`, `input_tokens`, `output_tokens`

If stats are present, include them in the output. If missing, set `performance` to `null`.

Flag any anomalies (informational only):
- `no_stats`: stats missing or all zeros
- `low_throughput`: tokens_per_second < 5.0
- `high_latency`: time_to_first_token > 5.0s
- `token_explosion`: input_tokens > 4000

## OUTPUT FORMAT

```json
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
  "reasoning": "1-2 sentence explanation",
  "performance": {
    "tokens_per_second": N.N or null,
    "time_to_first_token": N.NNN or null,
    "input_tokens": N or null,
    "output_tokens": N or null,
    "flags": [] or ["no_stats", "low_throughput", "high_latency", "token_explosion"]
  }
}
```
