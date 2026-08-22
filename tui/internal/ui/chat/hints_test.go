// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The whole point of ranking: the narrower the window, the more the user needs
// the way out and the less they need to be told the wheel scrolls. A plain
// right-hand truncation does the exact opposite.
func TestANarrowBarKeepsTheWayOutAndDropsTheNiceties(t *testing.T) {
	hints := []hint{
		{text: "step 12", rank: rankDiagnostic},
		{text: "↑↓ scroll", rank: rankAffordance},
		{text: "Esc cancel", rank: rankInterrupt},
		{text: "Ctrl+C quit", rank: rankEscape},
	}

	for _, width := range []int{11, 15, 24, 40} {
		got := fitHints(hints, width)
		if !strings.Contains(got, "Ctrl+C quit") {
			t.Errorf("width %d dropped the escape hatch: %q", width, got)
		}
		if w := ansi.StringWidth(got); w > width {
			t.Errorf("width %d overflowed to %d columns: %q", width, w, got)
		}
		if strings.Contains(got, "…") {
			t.Errorf("width %d truncated an item instead of dropping one: %q", width, got)
		}
	}

	// Tight: only the escape hatch survives, and the diagnostic goes first.
	tight := fitHints(hints, 12)
	if tight != "Ctrl+C quit" {
		t.Errorf("at 12 columns the bar reads %q; want just the way out", tight)
	}

	// Roomy: everything shows, in the order it was appended.
	full := fitHints(hints, 80)
	if full != "step 12 · ↑↓ scroll · Esc cancel · Ctrl+C quit" {
		t.Errorf("a wide bar changed the order or lost an item: %q", full)
	}
}

// Ranks decide the order of loss, not the order of display.
func TestItemsAreDroppedByRankNotByPosition(t *testing.T) {
	hints := []hint{
		{text: "step 3", rank: rankDiagnostic},
		{text: "End to jump to latest", rank: rankOrient},
		{text: "Ctrl+C quit", rank: rankEscape},
	}
	got := fitHints(hints, 36)
	if strings.Contains(got, "step 3") {
		t.Errorf("the diagnostic outlived the orientation hint: %q", got)
	}
	if !strings.Contains(got, "End to jump to latest") {
		t.Errorf("the way back to the newest content was dropped too early: %q", got)
	}
}

// A terminal too narrow for even one item gets that item whole, clipped by the
// bar. Returning an empty string would be worse — the bar would say nothing.
func TestAnImpossiblyNarrowBarStillNamesTheWayOut(t *testing.T) {
	got := fitHints([]hint{
		{text: "↑↓ scroll", rank: rankAffordance},
		{text: "Ctrl+C quit", rank: rankEscape},
	}, 3)
	if got != "Ctrl+C quit" {
		t.Errorf("got %q; want the escape hatch alone", got)
	}
}

// The measure has to be display columns: "↑↓ scroll · Ctrl+C quit" is 23
// columns but 27 bytes, and a byte count drops an item that would have fit.
func TestHintsAreMeasuredInColumnsNotBytes(t *testing.T) {
	hints := []hint{
		{text: "↑↓ scroll", rank: rankAffordance},
		{text: "Ctrl+C quit", rank: rankEscape},
	}
	full := "↑↓ scroll · Ctrl+C quit"
	if got := fitHints(hints, ansi.StringWidth(full)); got != full {
		t.Errorf("both items fit in %d columns but got %q", ansi.StringWidth(full), got)
	}
	if len(full) <= ansi.StringWidth(full) {
		t.Fatal("test premise broken: the string is no longer multi-byte")
	}
}

// The step counter is machinery, not progress — it stays out of a normal
// session entirely.
func TestTheStepCounterIsDevOnly(t *testing.T) {
	m := newTestChat(t)
	m.totalSteps = 7

	for _, h := range m.statusHints() {
		if strings.HasPrefix(h.text, "step ") {
			t.Errorf("the agent loop's step count leaked into a normal session: %q", h.text)
		}
	}

	m.dev = true
	var found bool
	for _, h := range m.statusHints() {
		if h.text == "step 7" {
			found = true
		}
	}
	if !found {
		t.Error("--dev lost the step counter")
	}
}

// Each state advertises what actually applies in it.
func TestHintsMatchTheState(t *testing.T) {
	busy := newTestChat(t)
	if got := joinHints(busy.statusHints()); !strings.Contains(got, "keep typing") ||
		!strings.Contains(got, "Esc cancel") {
		t.Errorf("a running turn does not advertise type-ahead or cancel: %q", got)
	}

	idle := newTestChat(t)
	idle.streaming = false
	if got := joinHints(idle.statusHints()); strings.Contains(got, "Esc cancel") {
		t.Errorf("an idle session offers to cancel nothing: %q", got)
	}

	scrolled := newTestChat(t)
	scrolled.followTail = false
	if got := joinHints(scrolled.statusHints()); !strings.Contains(got, "End to jump to latest") {
		t.Errorf("a scrolled-away reader is not told the way back: %q", got)
	}
}

func joinHints(hints []hint) string {
	var out []string
	for _, h := range hints {
		out = append(out, h.text)
	}
	return strings.Join(out, hintSeparator)
}
