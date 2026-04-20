<!--
Canonical prompt for §15.8 P9 — conversational intent classifier.

Loaded by :mod:`gaia.coder.intent.build_prompt`. When this file is edited,
the matching test `test_intent_classifier_maps_canonical_phrases` must
still pass — the model contract is stable across the intent-table renderer.
Model: Claude Opus 4.7, temperature 0, max 200 output tokens.
Fires: every `gaia-coder ask "..."` (§15.4).
-->
Classify engineering-manager messages into intents defined in §15.4 of
docs/plans/coder-agent.mdx.

Intents:
{{intent_table_injected}}

Message: """{{em_message}}"""

JSON only:
{"intent":"<name>","args":{...},"confidence":<0-100>}

If no intent matches with confidence ≥ 70, respond:
{"intent":"free_form","args":{},"confidence":0}
