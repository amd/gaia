package chat

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

// turnMetricsBlock is the --dev breakdown drawn under an answer: where the
// turn's seconds went, and how much of its input the KV cache could reuse.
//
// The one number the stats line cannot carry is the reason a local turn is
// slow — the fixed prefill re-sent on every call. This block exists to make
// that visible next to the wall time it buys.
//
// Returns "" whenever there is nothing real to draw: dev mode off, no record
// (an ordinary turn, or an agent older than gaia.turn/1), or a record that
// carries no measurement — rows of zeroes read as a broken turn, not an
// unmeasured one.
func (m ChatModel) turnMetricsBlock(msg *Message) string {
	if !m.dev || msg == nil || msg.Metrics == nil {
		return ""
	}
	t := msg.Metrics
	if t.TotalS <= 0 && len(t.LLMCalls) == 0 {
		return ""
	}
	var lines []string

	header := fmt.Sprintf("turn %s  %s → %s  %.1fs total",
		shortTurnID(t.TurnID), clockOf(t.StartedAt), clockOf(t.EndedAt), t.TotalS)
	lines = append(lines, header)

	lines = append(lines, fmt.Sprintf("model %.1fs · tools %.1fs · overhead %.1fs",
		t.Totals.LLMS, t.Totals.ToolS, t.Totals.OverheadS))

	in, cached, fresh := turnTokenSplit(t)
	tokenLine := fmt.Sprintf("in %s tok (%s cached, %s new · %s hit) · out %s tok",
		commas(in), commas(cached), commas(fresh),
		hitRate(cached, in), commas(int(t.Totals.OutputTokensServer)))
	lines = append(lines, tokenLine)

	shape := fmt.Sprintf("prefill %s fixed · %d tools",
		thousands(t.Prompt.FixedPrefillTokens), t.Prompt.ToolsSent)
	if len(t.Prompt.SkillsActive) > 0 {
		shape += " · skills: " + strings.Join(t.Prompt.SkillsActive, ", ")
	}
	lines = append(lines, shape)

	lines = append(lines, stepLines(t)...)

	out := make([]string, 0, len(lines))
	for _, l := range lines {
		out = append(out, devPayloadStyle.Render("  "+l))
	}
	return strings.Join(out, "\n")
}

// turnTokenSplit picks the honest cached/new split for the turn.
//
// A backend that reports its own cache accounting — Anthropic's prefix cache
// does, a local llama.cpp KV cache does not — is ground truth. A cold turn
// that only *wrote* the cache counts as reporting, so the first turn and the
// second are drawn from the same source and their totals are comparable.
//
// The local estimate is the stand-in for backends that report nothing, and it
// is blind to a cache that spans turns: it compares each call against the
// previous one *within* the record, so the first call of every turn reads as
// 0% no matter what the server reused.
func turnTokenSplit(t *event.CanonicalTurnStats) (total, cached, fresh int) {
	reported := t.Totals.InputTokensCachedServer > 0 || t.Totals.CacheWriteTokensServer > 0
	if reported && t.Totals.InputTokensServer > 0 {
		total = t.Totals.InputTokensServer
		cached = t.Totals.InputTokensCachedServer
	} else {
		total = t.Totals.InputTokensLocal
		cached = t.Totals.InputTokensCachedLocal
	}
	fresh = total - cached
	if fresh < 0 {
		fresh = 0
	}
	return total, cached, fresh
}

// callTokenSplit is turnTokenSplit for a single call.
func callTokenSplit(c event.CanonicalTurnCall) (total, cached int) {
	if (c.CacheReadInputTokens > 0 || c.CacheCreationInputTokens > 0) && c.InputTokens > 0 {
		return int(c.InputTokens), int(c.CacheReadInputTokens)
	}
	return c.InputTokensLocal, c.InputTokensCached
}

// stepLines draws one row per backend call, with that step's tool time hung on
// the end. Per-step is the resolution that matters: a turn is slow because one
// step re-read the whole prompt, and an aggregate hides which.
func stepLines(t *event.CanonicalTurnStats) []string {
	toolsByStep := map[int][]string{}
	for _, c := range t.ToolCalls {
		label := fmt.Sprintf("%s %.1fs", c.Name, c.WallS)
		if !c.OK {
			label += " ✗"
		}
		toolsByStep[c.Step] = append(toolsByStep[c.Step], label)
	}

	lines := make([]string, 0, len(t.LLMCalls))
	for _, c := range t.LLMCalls {
		stepIn, stepCached := callTokenSplit(c)
		row := fmt.Sprintf("step %-2d %5.1fs  ttft %4.1fs  in %s (%s cached)",
			c.Step, c.WallS, c.TTFTS, commas(stepIn), commas(stepCached))
		// Prefill rate over the tokens the server actually had to read. Far
		// above the cold rate means the cache prefix was accepted.
		if c.PrefillTokPerS > 0 {
			row += fmt.Sprintf("  %.0f tok/s prefill", c.PrefillTokPerS)
		}
		lines = append(lines, row)
		// Tools hang under their step rather than trailing it: a step with
		// both a prefill rate and a tool ran past 80 columns on one line, and
		// a wrapped metrics row is unreadable.
		for _, tool := range toolsByStep[c.Step] {
			lines = append(lines, "        └ "+tool)
		}
	}
	return lines
}

// clockOf renders an ISO-8601 instant as a bare UTC wall clock, so a turn can
// be lined up against Lemonade's own log. An unparseable stamp is shown
// verbatim rather than replaced with a plausible-looking time.
func clockOf(iso string) string {
	if iso == "" {
		return "?"
	}
	ts, err := time.Parse(time.RFC3339, iso)
	if err != nil {
		return iso
	}
	return ts.UTC().Format("15:04:05Z")
}

// shortTurnID trims the record's id to the prefix that stays greppable in the
// log file while fitting the header.
func shortTurnID(id string) string {
	if len(id) > 6 {
		return id[:6]
	}
	if id == "" {
		return "?"
	}
	return id
}

// hitRate is the share of input tokens the cache could have reused. "n/a" when
// nothing was counted — a zero percent would read as a total cache miss.
func hitRate(cached, total int) string {
	if total <= 0 {
		return "n/a"
	}
	return fmt.Sprintf("%.0f%%", 100*float64(cached)/float64(total))
}

// commas groups a token count for reading at a glance: five-digit prompt sizes
// are the norm here and 51204 does not parse as fast as 51,204.
func commas(n int) string {
	s := strconv.Itoa(n)
	neg := strings.HasPrefix(s, "-")
	if neg {
		s = s[1:]
	}
	var b strings.Builder
	for i, r := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(r)
	}
	if neg {
		return "-" + b.String()
	}
	return b.String()
}

// thousands renders a token count as "17.0k" — the scale that matters for
// prefill, where the difference between 17k and 6k is the whole finding.
func thousands(n int) string {
	if n < 1000 {
		return strconv.Itoa(n)
	}
	return fmt.Sprintf("%.1fk", float64(n)/1000)
}
