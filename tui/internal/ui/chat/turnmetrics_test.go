package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

func sampleTurnStats() *event.CanonicalTurnStats {
	return &event.CanonicalTurnStats{
		TurnID:    "a3f1c2d4e5f6",
		Model:     "Gemma-4-E4B-it-GGUF",
		StartedAt: "2026-08-18T22:47:35.120000+00:00",
		EndedAt:   "2026-08-18T22:48:09.640000+00:00",
		TotalS:    34.52,
		Steps:     2,
		Prompt: event.CanonicalTurnPrompt{
			FixedPrefillTokens: 17004,
			ToolsSent:          66,
			SkillsActive:       []string{"gaia-voice"},
		},
		LLMCalls: []event.CanonicalTurnCall{
			{Step: 1, WallS: 12.8, TTFTS: 4.9, InputTokensLocal: 17204, InputTokensNew: 17204, PrefillTokPerS: 3511},
			{Step: 2, WallS: 9.1, TTFTS: 0.4, InputTokensLocal: 17260, InputTokensCached: 17204},
		},
		ToolCalls: []event.CanonicalTurnTool{
			{Step: 1, Name: "run_shell_command", WallS: 2.1, OK: true},
		},
		Totals: event.CanonicalTurnTotals{
			LLMS: 28.4, ToolS: 4.8, OverheadS: 1.32,
			InputTokensLocal: 51204, InputTokensCachedLocal: 38110,
			InputTokensNewLocal: 13094, OutputTokensServer: 210,
		},
	}
}

func sampleMetricsMessage() *Message {
	return &Message{
		Role:      RoleAssistant,
		Content:   "the answer",
		Duration:  34520 * time.Millisecond,
		TTFT:      2100 * time.Millisecond,
		Tokens:    210,
		Steps:     2,
		ToolsUsed: 1,
		Metrics:   sampleTurnStats(),
	}
}

// The whole point of the record is that the numbers explaining a slow turn —
// where its seconds went, how much of its input the cache could reuse, and how
// big the fixed prefill is — actually reach the screen.
func TestTurnMetricsBlockShowsTheBreakdown(t *testing.T) {
	dev := NewChatModel(&nullClient{}, "GAIA", "", true)
	block := dev.turnMetricsBlock(sampleMetricsMessage())
	if block == "" {
		t.Fatal("--dev with a record drew nothing")
	}
	for _, want := range []string{
		"turn a3f1c2", // greppable against the log file
		"22:47:35Z",   // absolute timestamps, per the ask
		"34.5s total", // submit-to-answer
		"model 28.4s", // wall-time split
		"tools 4.8s",
		"overhead 1.3s",
		"51,204 tok", // input, cached vs new
		"38,110 cached",
		"13,094 new",
		"74% hit",
		"out 210 tok",
		"17.0k fixed", // the prefill that explains the latency
		"66 tools",
		"gaia-voice",
		"step 1",
		"step 2",
		"3511 tok/s prefill",
		"run_shell_command 2.1s",
	} {
		if !strings.Contains(block, want) {
			t.Errorf("block is missing %q:\n%s", want, block)
		}
	}
	for _, line := range strings.Split(block, "\n") {
		if got := len([]rune(stripANSI(line))); got > 80 {
			t.Errorf("line runs past 80 columns (%d): %q", got, line)
		}
	}
}

// Two ways there is no record: dev mode off, and an agent that never sent one.
// Both must draw nothing at all — an empty block would leave a stray blank
// line under every answer.
func TestTurnMetricsBlockAbsent(t *testing.T) {
	cases := []struct {
		name string
		dev  bool
		msg  *Message
	}{
		{"dev off", false, sampleMetricsMessage()},
		{"older agent sent no record", true, &Message{Role: RoleAssistant, Duration: time.Second}},
		{"nil message", true, nil},
		// A record that decoded but measured nothing. Drawing it would put
		// four rows of zeroes under the answer, which reads as a broken turn
		// rather than an unmeasured one.
		{"record carries no measurement", true, &Message{
			Role: RoleAssistant, Duration: time.Second,
			Metrics: &event.CanonicalTurnStats{},
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := NewChatModel(&nullClient{}, "GAIA", "", tc.dev)
			if got := m.turnMetricsBlock(tc.msg); got != "" {
				t.Errorf("expected nothing drawn, got:\n%s", got)
			}
		})
	}
}

// Prefill joins the --dev stats line; the quiet line stays the single elapsed
// figure it was deliberately reduced to (#2899).
func TestPrefillOnTheStatsLine(t *testing.T) {
	msg := sampleMetricsMessage()

	dev := NewChatModel(&nullClient{}, "GAIA", "", true)
	if got := dev.answerStats(msg); !strings.Contains(got, "17.0k prefill") {
		t.Errorf("--dev stats line lost the prefill size: %q", got)
	}
	quiet := NewChatModel(&nullClient{}, "GAIA", "", false)
	if got := quiet.answerStats(msg); got != "34.5s" {
		t.Errorf("quiet stats line = %q; a record must not change it", got)
	}
}

// An unparseable timestamp is shown verbatim rather than replaced by a
// plausible-looking time — a wrong clock is worse than a visibly odd one.
func TestClockOfKeepsUnparseableStampsHonest(t *testing.T) {
	if got := clockOf("not-a-time"); got != "not-a-time" {
		t.Errorf("clockOf(%q) = %q, want it passed through", "not-a-time", got)
	}
	if got := clockOf(""); got != "?" {
		t.Errorf("clockOf(\"\") = %q, want %q", got, "?")
	}
}

func TestCommasAndThousands(t *testing.T) {
	for in, want := range map[int]string{0: "0", 999: "999", 1000: "1,000", 51204: "51,204", 1234567: "1,234,567"} {
		if got := commas(in); got != want {
			t.Errorf("commas(%d) = %q, want %q", in, got, want)
		}
	}
	for in, want := range map[int]string{0: "0", 999: "999", 1000: "1.0k", 17004: "17.0k"} {
		if got := thousands(in); got != want {
			t.Errorf("thousands(%d) = %q, want %q", in, got, want)
		}
	}
}

// stripANSI removes SGR escapes so a styled line can be measured in columns.
func stripANSI(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == 0x1b {
			for i < len(s) && s[i] != 'm' {
				i++
			}
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

// A backend with a real prefix cache (Anthropic) reports what it actually
// reused. The local estimator cannot see across turns — it resets with each
// record — so on a one-step turn it always says 0%, which is exactly the
// reading that made the cache look broken when it was working.
func TestServerCacheCountersWinOverTheLocalEstimate(t *testing.T) {
	stats := sampleTurnStats()
	stats.LLMCalls = []event.CanonicalTurnCall{
		{Step: 1, WallS: 0.7, InputTokens: 14057, CacheReadInputTokens: 13696},
	}
	stats.Totals = event.CanonicalTurnTotals{
		InputTokensLocal: 12895, InputTokensCachedLocal: 0, InputTokensNewLocal: 12895,
		InputTokensServer: 14057, InputTokensCachedServer: 13696,
		OutputTokensServer: 5,
	}
	msg := sampleMetricsMessage()
	msg.Metrics = stats

	block := NewChatModel(&nullClient{}, "GAIA", "", true).turnMetricsBlock(msg)
	for _, want := range []string{"14,057 tok", "13,696 cached", "361 new", "97% hit"} {
		if !strings.Contains(block, want) {
			t.Errorf("block is missing %q:\n%s", want, block)
		}
	}
	if strings.Contains(block, "12,895") {
		t.Errorf("local estimate leaked into a turn the backend measured:\n%s", block)
	}
}

// A cold turn only writes the cache. It still counts as measured, so turn 1 and
// turn 2 are drawn from the same source and their totals compare directly.
func TestAColdTurnStillUsesTheServerTotals(t *testing.T) {
	stats := sampleTurnStats()
	stats.LLMCalls = nil
	stats.Totals = event.CanonicalTurnTotals{
		InputTokensLocal: 12858, InputTokensCachedLocal: 0,
		InputTokensServer: 14034, CacheWriteTokensServer: 13696,
	}
	msg := sampleMetricsMessage()
	msg.Metrics = stats

	block := NewChatModel(&nullClient{}, "GAIA", "", true).turnMetricsBlock(msg)
	if !strings.Contains(block, "14,034 tok") || !strings.Contains(block, "0 cached") {
		t.Errorf("cold turn did not report the server total:\n%s", block)
	}
}

// Lemonade reports neither counter, so the local prefix estimate must survive —
// it is the only cached/new signal a local llama.cpp turn has.
func TestLocalEstimateSurvivesOnABackendThatReportsNoCache(t *testing.T) {
	block := NewChatModel(&nullClient{}, "GAIA", "", true).turnMetricsBlock(sampleMetricsMessage())
	for _, want := range []string{"51,204 tok", "38,110 cached", "74% hit"} {
		if !strings.Contains(block, want) {
			t.Errorf("block is missing %q:\n%s", want, block)
		}
	}
}
