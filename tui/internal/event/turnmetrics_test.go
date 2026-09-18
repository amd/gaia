package event

import "testing"

// The per-turn record is optional on the wire: it rides `usage.metrics` only
// when the agent ran with GAIA_TURN_LOG set. An agent that never sends one —
// every ordinary turn, and every agent older than gaia.turn/1 — must decode to
// a nil Metrics rather than an error or a zero-valued block the UI would draw.
func TestCanonicalUsageMetrics(t *testing.T) {
	const full = `{"type":"final","answer":"done","usage":{"steps":3,"tools_used":1,"tokens":210,"ttft":2.1,` +
		`"metrics":{"schema":"gaia.turn/1","turn_id":"a3f1c2d4e5f6","model":"Gemma-4-E4B-it-GGUF",` +
		`"started_at":"2026-08-18T22:47:35.120000+00:00","ended_at":"2026-08-18T22:48:09.640000+00:00",` +
		`"total_s":34.52,"steps":3,` +
		`"prompt":{"fixed_prefill_tokens":17004,"system_tokens":6757,"tool_schema_tokens":10247,` +
		`"tools_sent":66,"skills_active":["gaia-voice"]},` +
		`"llm_calls":[{"step":1,"at":"2026-08-18T22:47:35.200000+00:00","wall_s":12.8,"ttft_s":4.9,` +
		`"input_tokens_local":17204,"input_tokens_cached":0,"input_tokens_new":17204,` +
		`"output_tokens":48,"prefill_tok_per_s":3511.0}],` +
		`"tool_calls":[{"step":1,"name":"run_shell_command","wall_s":2.1,"ok":true}],` +
		`"totals":{"llm_s":28.4,"tool_s":4.8,"overhead_s":1.32,"input_tokens_local":51204,` +
		`"input_tokens_cached_local":38110,"input_tokens_new_local":13094,"output_tokens_server":210}}}}`

	cases := []struct {
		name        string
		frame       string
		wantMetrics bool
		check       func(t *testing.T, u CanonicalUsage)
	}{
		{
			name:        "record present",
			frame:       full,
			wantMetrics: true,
			check: func(t *testing.T, u CanonicalUsage) {
				m := u.Metrics
				if m.TurnID != "a3f1c2d4e5f6" || m.TotalS != 34.52 || m.Steps != 3 {
					t.Errorf("turn header decoded wrong: %+v", m)
				}
				if m.Prompt.FixedPrefillTokens != 17004 || m.Prompt.ToolsSent != 66 {
					t.Errorf("prompt shape decoded wrong: %+v", m.Prompt)
				}
				if len(m.Prompt.SkillsActive) != 1 || m.Prompt.SkillsActive[0] != "gaia-voice" {
					t.Errorf("skills decoded wrong: %+v", m.Prompt.SkillsActive)
				}
				if m.Totals.InputTokensCachedLocal != 38110 || m.Totals.OutputTokensServer != 210 {
					t.Errorf("totals decoded wrong: %+v", m.Totals)
				}
				if len(m.LLMCalls) != 1 || m.LLMCalls[0].PrefillTokPerS != 3511.0 {
					t.Errorf("llm calls decoded wrong: %+v", m.LLMCalls)
				}
				if len(m.ToolCalls) != 1 || m.ToolCalls[0].Name != "run_shell_command" || !m.ToolCalls[0].OK {
					t.Errorf("tool calls decoded wrong: %+v", m.ToolCalls)
				}
				// The ordinary stats must survive alongside the record.
				if u.Steps != 3 || u.Tokens != 210 || u.TTFT != 2.1 {
					t.Errorf("base usage clobbered by metrics: %+v", u)
				}
			},
		},
		{
			name:  "older agent, no record",
			frame: `{"type":"final","answer":"done","usage":{"steps":3,"tools_used":1,"tokens":210,"ttft":2.1}}`,
			check: func(t *testing.T, u CanonicalUsage) {
				if u.Steps != 3 || u.Tokens != 210 {
					t.Errorf("base usage lost: %+v", u)
				}
			},
		},
		{
			name:  "no usage at all",
			frame: `{"type":"final","answer":"done"}`,
		},
		{
			name:        "record present but empty",
			frame:       `{"type":"final","answer":"done","usage":{"steps":2,"metrics":{}}}`,
			wantMetrics: true,
			check: func(t *testing.T, u CanonicalUsage) {
				if u.Steps != 2 {
					t.Errorf("base usage lost: %+v", u)
				}
				if u.Metrics.Prompt.FixedPrefillTokens != 0 || len(u.Metrics.LLMCalls) != 0 {
					t.Errorf("empty record should stay zero-valued: %+v", u.Metrics)
				}
			},
		},
		{
			// The record is a passthrough of whatever the agent emits, so a
			// type we misread inside it must cost only the breakdown. Losing
			// steps/tokens/ttft too would break every turn's stats line on one
			// bad field.
			name: "record is malformed",
			frame: `{"type":"final","answer":"done","usage":{"steps":3,"tokens":210,"ttft":2.1,` +
				`"metrics":{"total_s":"thirty-four","tool_calls":{"not":"an array"}}}}`,
			check: func(t *testing.T, u CanonicalUsage) {
				if u.Steps != 3 || u.Tokens != 210 || u.TTFT != 2.1 {
					t.Errorf("a bad record erased the turn's own stats: %+v", u)
				}
			},
		},
		{
			// Same rule one level up: usage itself unreadable loses everything,
			// which is the pre-existing contract, but must not panic.
			name:  "usage is not an object",
			frame: `{"type":"final","answer":"done","usage":"nope"}`,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f, ok := ParseCanonicalEvent([]byte(tc.frame)).(CanonicalFinalEvent)
			if !ok {
				t.Fatalf("frame did not parse as a final event")
			}
			u := CanonicalUsageOf(f)
			if (u.Metrics != nil) != tc.wantMetrics {
				t.Fatalf("Metrics present = %v, want %v", u.Metrics != nil, tc.wantMetrics)
			}
			if tc.check != nil {
				tc.check(t, u)
			}
		})
	}
}
