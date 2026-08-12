// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The height budget is a contract, not an aspiration: the panel is drawn over
// the live view, so a body taller than its box loses rows off BOTH ends —
// lipgloss.Place centres first and clips second. Counting the constant is the
// only way to notice a line was added; counting it by eye is how the chat panel
// drifted out of date in the first place.
func TestHelpTextFitsItsBudget(t *testing.T) {
	// hubHelpText's comment sets the budget: 20 body lines, plus a border row
	// and a padding row at each end, is 24 — the shortest terminal the TUI aims
	// to be usable in. Inner width is the 60-column cap less 2 padding columns
	// on each side.
	const (
		maxLines = 20
		maxWidth = helpBoxMaxWidth - 4
	)

	for name, text := range map[string]string{"hub": hubHelpText, "chat": chatHelpText} {
		lines := strings.Split(text, "\n")
		if len(lines) > maxLines {
			t.Errorf("%s help is %d lines, over the %d-line budget", name, len(lines), maxLines)
		}
		for i, line := range lines {
			if w := ansi.StringWidth(line); w > maxWidth {
				t.Errorf("%s help line %d is %d columns, over %d — it will soft-wrap and cost an extra row: %q",
					name, i+1, w, maxWidth, line)
			}
		}
	}
}

// Everything the chat view answers to has to be in here. The panel claimed only
// Enter / Esc / Ctrl+C / PgUp-PgDn long after the transcript gained line
// scrolling, half-page scrolling, jump-to-end, the mouse wheel, and a queue for
// typing while the agent is mid-turn.
func TestChatHelpNamesEveryChatBinding(t *testing.T) {
	for _, want := range []string{
		"Enter", "queues", // type-ahead: Enter during a turn queues the message
		"Esc",     // cancel a streaming turn, and back to the hub when idle
		"Give up", // a second Esc stops waiting on the cancel
		"Ctrl+C",
		"↑", "↓", // one line at a time
		"PgUp", "PgDn", // half a page
		"Home", "End", "composer", // only when the composer is empty
		"Mouse wheel",
		"/help", "/hub", "/clear",
	} {
		if !strings.Contains(chatHelpText, want) {
			t.Errorf("chat help never mentions %q", want)
		}
	}
}

// The overlay is composited over the live view, so it must return exactly the
// screen it was handed — no taller, no wider, whatever the window size.
func TestHelpOverlayNeverOutgrowsTheWindow(t *testing.T) {
	sizes := []struct{ w, h int }{
		{100, 40}, // roomy
		{80, 24},  // the size the budget is written for
		{80, 14},  // one row under the budget: padding has to go
		{80, 10},  // shorter than the body: lines have to go
		{80, 6},
		{40, 24}, // narrow enough that lines need truncating
		{20, 12},
	}
	background := strings.TrimSuffix(strings.Repeat("background\n", 40), "\n")

	for _, ctx := range []HelpContext{HelpContextHub, HelpContextChat} {
		for _, s := range sizes {
			out := RenderHelpOverlay(ctx, background, s.w, s.h)
			rows := strings.Split(out, "\n")
			if len(rows) != s.h {
				t.Errorf("ctx %d at %dx%d rendered %d rows, want %d", ctx, s.w, s.h, len(rows), s.h)
				continue
			}
			for i, row := range rows {
				if w := ansi.StringWidth(row); w != s.w {
					t.Errorf("ctx %d at %dx%d: row %d is %d columns, want %d", ctx, s.w, s.h, i, w, s.w)
					break
				}
			}
		}
	}
}

// Cutting the list is fine; cutting it silently is not — a reader who cannot
// see the shortcut they came for needs to know it exists.
func TestAShortWindowSaysTheHelpWasCut(t *testing.T) {
	out := ansi.Strip(RenderHelpOverlay(HelpContextChat, "", 80, 10))
	if !strings.Contains(out, "window too short") {
		t.Errorf("the panel was truncated with nothing saying so:\n%s", out)
	}
	if !strings.Contains(out, "GAIA Chat") {
		t.Errorf("the title was clipped away, so the panel lost its top:\n%s", out)
	}
}

// A window with no room for a panel gets the view it already had, not a box
// with one column of border in it.
func TestATinyWindowKeepsTheViewItHas(t *testing.T) {
	const background = "the chat view"
	if got := RenderHelpOverlay(HelpContextChat, background, 8, 20); got != background {
		t.Errorf("a 8-column window rendered a panel anyway: %q", got)
	}
	if got := RenderHelpOverlay(HelpContextChat, background, 80, 2); got != background {
		t.Errorf("a 2-row window rendered a panel anyway: %q", got)
	}
}
