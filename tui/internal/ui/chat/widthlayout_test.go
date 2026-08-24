// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// terminalSizes spans the range a user actually runs the TUI at: a laptop
// half-screen, the classic 80, a maximised window, and an ultrawide.
var terminalSizes = []struct {
	name          string
	width, height int
}{
	{"cramped", 24, 10},
	{"tiny", 40, 12},
	{"narrow", 60, 20},
	{"classic", 80, 24},
	{"wide", 120, 40},
	{"wider", 160, 45},
	{"ultrawide", 240, 55},
}

func sizedChat(t *testing.T, w, h int) ChatModel {
	t.Helper()
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = w, h
	m.resize()
	m.streaming = true
	m.queryStart = time.Now()
	return m
}

// The report this came from: on a wide terminal the work log still cut lines at
// the same column an 80-column terminal did, so the window's extra space bought
// the reader nothing.
func TestLogWidthFollowsTheTerminal(t *testing.T) {
	for _, tc := range terminalSizes {
		t.Run(tc.name, func(t *testing.T) {
			m := sizedChat(t, tc.width, tc.height)
			got := m.logWidth()
			want := tc.width - 6
			if want < 16 {
				want = 16
			}
			// The floor never wins past what the row can actually hold: the
			// 4-column marker gutter comes off the terminal either way.
			if fits := tc.width - 4; want > fits {
				want = fits
			}
			if got != want {
				t.Errorf("logWidth() = %d on a %d-column terminal, want %d", got, tc.width, want)
			}
		})
	}

	// The point of the change, stated as a comparison: more columns must mean
	// more room, not the same fixed cap twice.
	narrow := sizedChat(t, 80, 24).logWidth()
	wide := sizedChat(t, 240, 55).logWidth()
	if wide <= narrow {
		t.Errorf("a 240-column terminal budgets %d columns per log line, no more than an 80-column one at %d",
			wide, narrow)
	}
}

// minSupportedCols is the narrowest window the TUI claims to work at: the
// control API refuses a resize under it ("cols must be 20-500",
// control/server.go). It is a real floor, not a convenience — a live row spends
// 11 columns on decoration alone (gutter, spinner, the two-space gap and the
// clock), so below about 13 no arrangement of text fits and the question stops
// being about budgets.
const minSupportedCols = 20

// A cramped window must not shear. This RENDERS every row type at every width
// in the band rather than checking logWidth's arithmetic: an earlier version of
// this test asserted only that logWidth()+4 fits, which certified a property the
// rendered rows did not actually have — wrapLog was raising the measure back up
// to its own floor of 8, and the row printed past the last column anyway.
func TestNoRowShearsOnACrampedTerminal(t *testing.T) {
	for w := minSupportedCols; w <= 48; w++ {
		// The opening frame of a turn: the idle live line, before any event.
		idle := sizedChat(t, w, 10)
		checkRowsFit(t, w, "opening frame", idle.liveRegionView())

		// A turn under way: a live row with a clock, a wrapped action, an
		// outcome, and the "still working" hint, all at once.
		m := sizedChat(t, w, 10)
		m.dev = true
		m.queryStart = time.Now().Add(-2 * stillWorkingAfter)
		m = feed(t, m,
			event.CanonicalToolCallEvent{
				Type: "tool_call", Tool: "run_shell_command",
				Args: json.RawMessage(`{"command":"` + strings.Repeat("z", 200) + `"}`),
			},
		)
		checkRowsFit(t, w, "live row + hint", m.liveRegionView())

		// And with the action closed, so the outcome and --dev payload rows draw.
		m = feed(t, m, event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "run_shell_command",
			Preview: strings.Repeat("d", 200),
		})
		checkRowsFit(t, w, "closed action", m.liveRegionView())

		// A repeated call stacks the "xN" counter onto whichever row is live.
		rep := ActivityItem{Kind: "tool", Tool: "get_message", Content: strings.Repeat("m", 200), Repeat: 13}
		for _, live := range []bool{true, false} {
			checkRowsFit(t, w, "repeated action", strings.Join(
				sizedChat(t, w, 10).renderActivityItem(rep, live, 3720*time.Second, 0), "\n"))
		}
	}
}

// checkRowsFit fails if any rendered row is wider than the terminal drawing it.
// Nothing in this viewport soft-wraps, so an over-wide row takes the row below
// it with it.
func checkRowsFit(t *testing.T, width int, what, rendered string) {
	t.Helper()
	for _, line := range strings.Split(ansi.Strip(rendered), "\n") {
		if got := ansi.StringWidth(line); got > width {
			t.Errorf("%s: a row is %d columns wide on a %d-column terminal: %q",
				what, got, width, line)
		}
	}
}

// Prose deliberately does NOT follow the window: a 240-column terminal running
// body text to 236 columns loses the eye on the carriage return, and a question
// wrapped to the pane above an answer capped at 88 reads as two unrelated
// blocks.
func TestProseKeepsItsReadableMeasureAtEverySize(t *testing.T) {
	for _, tc := range terminalSizes {
		t.Run(tc.name, func(t *testing.T) {
			m := sizedChat(t, tc.width, tc.height)
			if w := m.answerWidth(); w > answerMeasure {
				t.Errorf("answers lay out at %d columns on a %d-column terminal; the cap is %d",
					w, tc.width, answerMeasure)
			}
			if w := m.answerWidth(); w > tc.width {
				t.Errorf("answer width %d overruns the %d-column terminal", w, tc.width)
			}
		})
	}
}

// longNarration is one tool line whose tail carries the part that matters — the
// shape of every line this change exists for.
const longNarration = "gh issue list --repo amd/gaia --state open --label bug " +
	"--json number,title,labels,updatedAt --jq select(updatedAt < 2026-01-01)"

// Every rendered work-log row must fit the window it is drawn in. Nothing here
// soft-wraps, so a row one column too wide shears the row beneath it.
func TestWorkLogRowsFitTheTerminal(t *testing.T) {
	for _, tc := range terminalSizes {
		t.Run(tc.name, func(t *testing.T) {
			m := sizedChat(t, tc.width, tc.height)
			m.dev = true
			m = feed(t, m,
				event.CanonicalToolCallEvent{
					Type:      "tool_call",
					Tool:      "run_shell_command",
					Args:      json.RawMessage(`{"command":"` + strings.Repeat("x", 400) + `"}`),
					Narration: longNarration,
				},
				event.CanonicalToolResultEvent{
					Type:    "tool_result",
					Tool:    "run_shell_command",
					Preview: strings.Repeat("outcome text that keeps going ", 20),
				},
				event.CanonicalToolCallEvent{
					Type: "tool_call",
					Tool: "query_documents",
					Args: json.RawMessage(`{"query":"` + strings.Repeat("q", 300) + `"}`),
				},
			)

			out := ansi.Strip(m.renderLiveRegion())
			for _, line := range strings.Split(out, "\n") {
				if w := ansi.StringWidth(line); w > tc.width {
					t.Errorf("a work-log row is %d columns wide on a %d-column terminal: %q",
						w, tc.width, line)
				}
			}
		})
	}
}

// The live line hangs a clock off its right edge. That clock is appended after
// the text is truncated, so it has to come out of the same budget — otherwise
// it is the one row that always overruns, on exactly the wide terminals this
// change opens up.
func TestTheLiveLineLeavesRoomForItsClock(t *testing.T) {
	for _, tc := range terminalSizes {
		t.Run(tc.name, func(t *testing.T) {
			m := sizedChat(t, tc.width, tc.height)
			item := ActivityItem{Kind: "tool", Tool: "run_shell_command", Content: strings.Repeat("z", 500)}
			for _, elapsed := range []time.Duration{9 * time.Second, 125 * time.Second, 3720 * time.Second} {
				for _, line := range m.renderActivityItem(item, true, elapsed, 0) {
					if w := ansi.StringWidth(ansi.Strip(line)); w > tc.width {
						t.Errorf("the live line is %d columns wide at %s on a %d-column terminal",
							w, formatElapsed(elapsed), tc.width)
					}
				}
			}
		})
	}
}

// A wider terminal has to actually SHOW more. Wrapping already saved the tail
// from being lost; a wider window is what stops it needing a second row at all,
// which is the difference between reading one line and reading a paragraph.
func TestAWiderTerminalShowsMoreOnOneRow(t *testing.T) {
	item := ActivityItem{Kind: "tool", Tool: "run_shell_command", Content: longNarration}
	rendered := func(w int) []string {
		m := sizedChat(t, w, 40)
		return m.renderActivityItem(item, false, 0, 0)
	}

	narrow, wide := rendered(80), rendered(240)
	if ansi.StringWidth(ansi.Strip(wide[0])) <= ansi.StringWidth(ansi.Strip(narrow[0])) {
		t.Fatalf("a 240-column terminal drew the same %d columns on its first row an 80-column one did",
			ansi.StringWidth(ansi.Strip(wide[0])))
	}
	// With room for the whole command, it is one row and the tail is on it.
	if len(wide) != 1 {
		t.Errorf("a 240-column terminal spent %d rows on a line that fits in one", len(wide))
	}
	if !strings.Contains(ansi.Strip(wide[0]), "2026-01-01") {
		t.Errorf("the tail of the command is missing on a 240-column terminal: %q", ansi.Strip(wide[0]))
	}
	// The same line does not fit an 80-column window, so it costs a second row
	// there — which is exactly the cost a wide window buys back.
	if len(narrow) < 2 {
		t.Errorf("an 80-column terminal fitted a %d-column line on one row", displayWidth(longNarration))
	}
}

// Capture must not pre-empt layout. Cutting the narration when the event
// arrived froze every line at 74 columns no matter how wide the window later
// turned out to be — the bug underneath all of the above.
func TestCaptureKeepsMoreThanOneScreensWorth(t *testing.T) {
	long := strings.Repeat("a", 300)
	if got := toolNarration("run_shell_command", json.RawMessage(`{"command":"`+long+`"}`), ""); displayWidth(got) <= 74 {
		t.Errorf("a shell command was captured at %d columns; layout can never widen it", displayWidth(got))
	}
	if got := toolNarration("some_tool", nil, long); displayWidth(got) <= 74 {
		t.Errorf("agent narration was captured at %d columns", displayWidth(got))
	}
	// Bounded, though: the model must not hold an unbounded blob off the wire.
	huge := strings.Repeat("b", 100000)
	if got := toolNarration("some_tool", nil, huge); displayWidth(got) > narrationMax {
		t.Errorf("narration captured at %d columns, past the %d bound", displayWidth(got), narrationMax)
	}
	detail := toolResultDetail(event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "t", Preview: huge,
	})
	if displayWidth(detail) > detailMax {
		t.Errorf("an outcome line captured at %d columns, past the %d bound", displayWidth(detail), detailMax)
	}
}

// A resize storm — dragging a window edge emits one WindowSizeMsg per frame —
// must not churn the markdown renderer, which SetWordWrap rebuilds on every
// width CHANGE. It cannot: the answer measure is capped, so every width past
// the cap asks for the same wrap and SetWordWrap short-circuits.
func TestResizingWideDoesNotChurnTheMarkdownWrap(t *testing.T) {
	m := sizedChat(t, 120, 40)
	first := m.answerWidth()
	for w := 121; w <= 240; w++ {
		m.width = w
		m.resize()
		if got := m.answerWidth(); got != first {
			t.Fatalf("answer width changed from %d to %d at %d columns; the wrap is capped and should be stable",
				first, got, w)
		}
	}
}

// The "still working" hint is part of the live region's height budget, which
// spends exactly one row on it. At 50 fixed columns it wrapped to two on a
// narrow terminal, so the region overran the budget that had already been
// decremented for it.
func TestTheStillWorkingHintFitsAndStaysOneRow(t *testing.T) {
	for _, tc := range terminalSizes {
		t.Run(tc.name, func(t *testing.T) {
			m := sizedChat(t, tc.width, tc.height)
			// No completed work and past the threshold: exactly the state the
			// hint exists for.
			m.queryStart = time.Now().Add(-2 * stillWorkingAfter)
			m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Working out how to answer"})

			out := ansi.Strip(m.renderLiveRegion())
			if !strings.Contains(out, "still working") {
				t.Fatalf("the hint did not render at all:\n%s", out)
			}
			for _, line := range strings.Split(out, "\n") {
				if w := ansi.StringWidth(line); w > tc.width {
					t.Errorf("a live-region row is %d columns wide on a %d-column terminal: %q",
						w, tc.width, line)
				}
			}
			if rows := len(strings.Split(out, "\n")); rows > m.logRows() {
				t.Errorf("the live region is %d rows on a %d-row terminal; the budget is %d",
					rows, tc.height, m.logRows())
			}
		})
	}
}

// Two reservations on one row: a repeated call's "x14" counter AND the live
// clock. Each is subtracted before truncating; together they still have to
// leave the row inside the window.
func TestAReservedCounterAndClockStack(t *testing.T) {
	for _, tc := range terminalSizes {
		t.Run(tc.name, func(t *testing.T) {
			m := sizedChat(t, tc.width, tc.height)
			item := ActivityItem{
				Kind: "tool", Tool: "get_message",
				Content: strings.Repeat("m", 400),
				Repeat:  13,
			}
			for _, line := range m.renderActivityItem(item, true, 3720*time.Second, 0) {
				if w := ansi.StringWidth(ansi.Strip(line)); w > tc.width {
					t.Errorf("a repeated live row is %d columns wide on a %d-column terminal: %q",
						w, tc.width, ansi.Strip(line))
				}
			}
		})
	}
}

// A resize reflows the work log, which before this change it could not: the
// measure was pinned at 74, so widening from 100 to 240 columns moved nothing.
// Now it moves 94 to 234 — straight into the peak-height hold, which is there to
// absorb the log shrinking under a reader mid-sentence. A deliberate resize is
// not that, and holding the old peak stranded blank rows under the log for the
// rest of the turn.
func TestResizingMidTurnStrandsNoBlankRows(t *testing.T) {
	m := sizedChat(t, 100, 44)
	m.queryStart = time.Now().Add(-95 * time.Second)
	args, _ := json.Marshal(map[string]string{"command": longNarration})
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "run_shell_command", Args: args},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "run_shell_command",
			Preview: "failed - permission denied; run gaia from a shell with write access to that folder",
		},
		event.CanonicalToolCallEvent{
			Type: "tool_call", Tool: "query_documents",
			Args: json.RawMessage(`{"query":"what did the Q3 board deck say about NPU attach rate targets"}`),
		},
	)

	// Widen, narrow, widen again — a window drag, not a tidy single step.
	for _, w := range []int{100, 240, 60, 240, 100} {
		m.width = w
		m.resize()
		rows := strings.Split(ansi.Strip(m.liveRegionView()), "\n")
		for i, r := range rows {
			if strings.TrimSpace(r) == "" {
				t.Errorf("at %d columns row %d of the live region is blank padding held over from another width:\n%s",
					w, i, strings.Join(rows, "\n"))
				break
			}
			if got := ansi.StringWidth(r); got > w {
				t.Errorf("at %d columns a row is %d columns wide: %q", w, got, r)
			}
		}
	}
}

// The hold itself must survive: it exists for the log growing and shrinking as
// EVENTS land, which is the case that yanks the transcript under a reader. Only
// the width case is released.
func TestTheHeightHoldStillAbsorbsEventChurn(t *testing.T) {
	m := sizedChat(t, 100, 44)
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "read_file",
			Args: json.RawMessage(`{"file_path":"` + strings.Repeat("some/long/path/", 12) + `x.go"}`)},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "read_file", Preview: "ok"},
	)
	tall := strings.Count(m.liveRegionView(), "\n") + 1

	// A cancel is the one thing allowed to drop the height, so use ordinary new
	// work: the region must not get SHORTER than it has already been.
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Thinking"})
	if got := strings.Count(m.liveRegionView(), "\n") + 1; got < tall {
		t.Errorf("the live region shrank from %d rows to %d while the turn was still running", tall, got)
	}
}

// resize() also runs when the COMPOSER grows a row, with the width unchanged.
// Releasing the height hold on every resize() rather than on a width change
// would let typing a multi-line follow-up collapse the log under the answer
// being read - the exact yank the hold exists to prevent.
func TestGrowingTheComposerDoesNotReleaseTheHold(t *testing.T) {
	m := sizedChat(t, 100, 44)
	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "read_file",
			Args: json.RawMessage(`{"file_path":"` + strings.Repeat("some/long/path/", 12) + `x.go"}`)},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "read_file", Preview: "ok"},
	)
	m.liveRegionView()
	peak := m.logPeakRows
	if peak == 0 {
		t.Fatal("no peak was recorded, so this proves nothing")
	}

	m.resize() // same width: a composer-height change, not a window resize
	if m.logPeakRows != peak {
		t.Errorf("a same-width resize dropped the held height from %d to %d", peak, m.logPeakRows)
	}
}

// The capture bound has to outrun the widest window this program will ever lay
// out, or a wide terminal re-clips text that capture already cut - which is the
// original bug, one layer up. 500 columns is the ceiling the control API's
// resize accepts, so that is the window to prove against.
func TestCaptureCoversTheWidestWindowTheTUIAccepts(t *testing.T) {
	const maxAcceptedCols = 500 // control/server.go: "cols must be 20-500"

	if got := sizedChat(t, maxAcceptedCols, 60).logWidth(); got > widestLogMeasure {
		t.Errorf("the widest accepted terminal lays rows out to %d columns, past the %d capture assumes",
			got, widestLogMeasure)
	}
	// And an action's whole wrapped body fits inside what capture kept.
	if rowsWorth := logHeadRows * widestLogMeasure; narrationMax < rowsWorth {
		t.Errorf("narrationMax %d cannot fill the %d rows the log will render", narrationMax, logHeadRows)
	}
	if rowsWorth := logDetailRows * widestLogMeasure; detailMax < rowsWorth {
		t.Errorf("detailMax %d cannot fill the %d rows an outcome will render", detailMax, logDetailRows)
	}
	if devPayloadWidth < widestLogMeasure {
		t.Errorf("a --dev payload is captured at %d columns but one row can show %d",
			devPayloadWidth, widestLogMeasure)
	}
}
