// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"
	"time"
)

// What a session cost, in the terms a person actually asks about.
//
// The per-turn footnote answers "how did THAT go". This answers "what has this
// session spent" — which is the question behind capacity planning, behind
// "is this task worth running on a cloud model", and behind every comparison
// between two ways of doing the same job.
//
// Everything here is summed from what the backend reported. Nothing is
// estimated: a turn whose backend reported no token counts contributes zero to
// the token totals and is still counted as a turn, and the view says how many
// turns went unmeasured rather than quietly averaging over them.

// turnCost is one turn's measured cost.
type turnCost struct {
	duration  time.Duration
	steps     int
	tools     int
	inTok     int
	outTok    int
	cachedTok int
	// measured is false when the backend reported no token counts at all, so
	// the totals can say how much of the session they actually cover.
	measured bool
}

// sessionCost accumulates turnCost across a session.
type sessionCost struct {
	turns []turnCost
}

func (s *sessionCost) add(t turnCost) { s.turns = append(s.turns, t) }

func (s sessionCost) totals() (d time.Duration, steps, tools, in, out, cached, measured int) {
	for _, t := range s.turns {
		d += t.duration
		steps += t.steps
		tools += t.tools
		in += t.inTok
		out += t.outTok
		cached += t.cachedTok
		if t.measured {
			measured++
		}
	}
	return
}

// render draws the session ledger. width is the pane it must fit.
func (s sessionCost) render(width int, model string, price *modelPrice) string {
	if len(s.turns) == 0 {
		return "No turns yet this session — nothing to cost."
	}
	d, steps, tools, in, out, cached, measured := s.totals()

	var b strings.Builder
	fmt.Fprintf(&b, "Session so far — %d turn%s on %s\n\n", len(s.turns), plural(len(s.turns)), model)
	fmt.Fprintf(&b, "  wall time     %s\n", compactDuration(d))
	fmt.Fprintf(&b, "  agent steps   %d\n", steps)
	fmt.Fprintf(&b, "  tool calls    %d\n", tools)

	if in+out == 0 {
		b.WriteString("\n  tokens        not reported by this backend\n")
		return b.String()
	}
	fmt.Fprintf(&b, "\n  input tokens  %s", thousands(in))
	if cached > 0 {
		fmt.Fprintf(&b, "   (%s served from cache, %d%%)", thousands(cached), pct(cached, in))
	}
	fmt.Fprintf(&b, "\n  output tokens %s\n", thousands(out))

	if measured < len(s.turns) {
		fmt.Fprintf(&b, "\n  %d of %d turns reported no token counts and are not in the totals above.\n",
			len(s.turns)-measured, len(s.turns))
	}

	// Money only with a price to apply. An invented rate in a cost readout is
	// worse than no number: it looks authoritative and nobody re-checks it.
	if price == nil {
		fmt.Fprintf(&b, "\n  cost          no price configured for %s — see /cost help\n", model)
		return b.String()
	}
	fmt.Fprintf(&b, "\n  cost          %s\n", price.format(in, cached, out))
	return b.String()
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

func pct(part, whole int) int {
	if whole <= 0 {
		return 0
	}
	return part * 100 / whole
}

// compactDuration renders a span the way a person says it back.
func compactDuration(d time.Duration) string {
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%.1fs", d.Seconds())
	case d < time.Hour:
		return fmt.Sprintf("%dm %ds", int(d.Minutes()), int(d.Seconds())%60)
	default:
		return fmt.Sprintf("%dh %dm", int(d.Hours()), int(d.Minutes())%60)
	}
}
