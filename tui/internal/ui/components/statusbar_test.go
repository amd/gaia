// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The bar is the last row of the screen, so it owes the layout exactly one line
// of exactly the width it was given. Measuring the two halves with len() broke
// both halves of that promise the moment the hint gained arrows and middots: a
// byte count reads `↑↓ scroll · Ctrl+C quit` as 27 columns when it draws 22, so
// the gap came out short and, on a narrow terminal, the content ran past the
// bar and lipgloss wrapped it onto a second row.
func TestStatusBarIsOneRowOfTheGivenWidth(t *testing.T) {
	cases := []struct {
		name  string
		state StatusBarState
		width int
	}{
		{"plain ascii hint", StatusBarState{AgentName: "gaia", Connected: true, Hint: "PgUp/PgDn scroll"}, 80},
		{"multi-byte hint", StatusBarState{AgentName: "gaia", Connected: true, Hint: "↑↓ scroll · ⏎ send"}, 80},
		{"wide runes", StatusBarState{AgentName: "gaia", Connected: true, Hint: "スクロール · 終了"}, 60},
		{"steps instead of a hint", StatusBarState{AgentName: "gaia", Connected: true, Steps: 7}, 40},
		{"no room for both halves", StatusBarState{AgentName: "gaia", Streaming: true, Hint: "↑↓ scroll · Esc cancel"}, 24},
		{"no room for anything", StatusBarState{AgentName: "gaia-agent-code", Hint: "↑↓ scroll"}, 8},
		{"one column", StatusBarState{AgentName: "gaia", Hint: "↑↓"}, 1},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			out := RenderStatusBar(tc.state, tc.width)
			if n := strings.Count(out, "\n"); n != 0 {
				t.Fatalf("the bar wrapped onto %d extra rows:\n%s", n, ansi.Strip(out))
			}
			if got := ansi.StringWidth(out); got != tc.width {
				t.Errorf("bar is %d columns wide, want %d: %q", got, tc.width, ansi.Strip(out))
			}
		})
	}
}

// The hint is right-aligned, and "right-aligned" has to mean the same number of
// blank columns whatever alphabet the hint is written in.
func TestTheHintSitsFlushAgainstTheRightEdge(t *testing.T) {
	// One column of air the bar adds itself, plus the one column of style
	// padding — the mirror of the leading space on the left.
	const trailingColumns = 2

	for _, hint := range []string{
		"PgUp/PgDn scroll",
		"↑↓ scroll · Ctrl+C quit",
		"End to jump to latest · ↑↓ scroll · Esc back",
	} {
		plain := ansi.Strip(RenderStatusBar(StatusBarState{AgentName: "gaia", Connected: true, Hint: hint}, 100))
		trimmed := strings.TrimRight(plain, " ")
		if !strings.HasSuffix(trimmed, hint) {
			t.Fatalf("the hint is not at the end of the bar: %q", plain)
		}
		if got := ansi.StringWidth(plain) - ansi.StringWidth(trimmed); got != trailingColumns {
			t.Errorf("hint %q ends %d columns from the right edge, want %d", hint, got, trailingColumns)
		}
	}
}

// Which agent is talking outranks how to scroll it, so the hint is what gives
// way when the two cannot both fit — shortened while there is still something
// worth reading, dropped once there is not.
func TestTheHintGivesWayBeforeTheAgentName(t *testing.T) {
	const state = "gaia-agent-code connected"
	hint := "↑↓ scroll · Esc back · Ctrl+C quit"

	shortened := ansi.Strip(RenderStatusBar(StatusBarState{
		AgentName: "gaia-agent-code", Connected: true, Hint: hint,
	}, 50))
	if !strings.Contains(shortened, state) {
		t.Errorf("the agent name was truncated before the hint was: %q", shortened)
	}
	if !strings.Contains(shortened, "…") || strings.Contains(shortened, "Ctrl+C quit") {
		t.Errorf("the hint was not shortened to fit: %q", shortened)
	}

	dropped := ansi.Strip(RenderStatusBar(StatusBarState{
		AgentName: "gaia-agent-code", Connected: true, Hint: hint,
	}, 34))
	if !strings.Contains(dropped, state) {
		t.Errorf("the agent name lost columns to a hint that could not fit anyway: %q", dropped)
	}
	if strings.Contains(dropped, "scroll") {
		t.Errorf("a hint with no room left was kept as a stub: %q", dropped)
	}
}
