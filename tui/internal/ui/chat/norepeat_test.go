// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// midTurn returns a model 65 seconds into a turn that has already finished one
// tool call — far enough in that both the live region and the composer row have
// something to say, and past the point where a bare stage line would add the
// "still working" reassurance and its own 60-90s to the screen.
func midTurn(t *testing.T) ChatModel {
	t.Helper()
	m := newTestChat(t)
	m.queryStart = time.Now().Add(-65 * time.Second)
	return feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_directory"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "list_directory", Preview: "26 files",
		},
	)
}

// One event, one line. The composer row used to mirror the live region, so a
// running turn put its action on screen twice — and, because the two rows
// formatted the same duration differently, with two clocks that disagreed:
//
//	⣟  Thinking about the next step  1:05
//	────────────────────────────────────────
//	⣟  ◆ Thinking about the next step  65s
//
// A reader seeing the same sentence twice with different numbers cannot tell
// whether one event is being reported twice or two things are happening.
func TestTheLiveActionIsRenderedExactlyOnce(t *testing.T) {
	m := midTurn(t)

	out := ansi.Strip(m.View())
	t.Logf("\n%s", out)

	phrase := m.idlePhrase(len(collapseActivity(m.activity)))
	if n := strings.Count(out, phrase); n != 1 {
		t.Errorf("%q is on screen %d times; the live region is its only home:\n%s",
			phrase, n, out)
	}
}

// Two clocks for one turn can only ever agree by luck: 1:05 and 65s were the
// same instant, rendered twice by two callers rounding it differently.
func TestOnlyOneElapsedClockIsOnScreen(t *testing.T) {
	m := midTurn(t)

	out := ansi.Strip(m.View())
	t.Logf("\n%s", out)

	// Both formats the turn clock has ever used: mm:ss and a bare seconds
	// count. Counted per row, so a future second clock is caught wherever it is
	// put rather than only in the composer row it was removed from.
	clocks := regexp.MustCompile(`\b\d+:\d{2}\b|\b\d+s\b`)
	var carrying []string
	for _, line := range strings.Split(out, "\n") {
		if clocks.MatchString(line) {
			carrying = append(carrying, strings.TrimSpace(line))
		}
	}
	if len(carrying) != 1 {
		t.Errorf("%d rows carry an elapsed clock, want 1: %q\n%s", len(carrying), carrying, out)
	}
}

// The step count reaches the screen through the --dev hint and nowhere else.
// The status bar can render it too, from its own field, and that path prints in
// user mode — it was suppressed only by the hint never happening to be empty.
func TestUserModeNeverShowsTheStepCount(t *testing.T) {
	m := midTurn(t)
	// Wide enough that the hint's lowest-ranked item survives the fit, so a
	// missing step count means it was never offered, not that it was thinned.
	m.width, m.height = 120, 30
	m.resize()
	m.totalSteps = 7

	if out := ansi.Strip(m.View()); strings.Contains(out, "step 7") ||
		strings.Contains(out, "steps: 7") {
		t.Errorf("the agent loop's step count leaked into user mode:\n%s", out)
	}

	m.dev = true
	if out := ansi.Strip(m.View()); !strings.Contains(out, "step 7") {
		t.Errorf("--dev lost the step count entirely:\n%s", out)
	}
}
