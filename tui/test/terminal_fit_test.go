package test

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// A frame taller than its terminal is not a cosmetic problem. The terminal
// scrolls to fit it, and Bubble Tea's repaint is cursor-relative — so from that
// moment on, every later frame lands in the wrong place.
//
// The screen that LOOKS broken is usually not the one that overflowed: a
// 26-row splash drawn before the first resize is what made the chat view render
// two composers, no header and no status bar. Which is why this checks every
// screen on the launch path, not just the one someone reported.
//
// Bubble Tea renders once BEFORE the first WindowSizeMsg, against an assumed
// 80x24, so the pre-resize frame is held to that.
const (
	assumedCols = 80
	assumedRows = 24
)

// terminalSizes spans what this has to survive: the standard minimum, windows
// smaller than anyone should use, a tall maximised one, and two degenerate
// shapes that catch layout maths that assumes a roughly 4:3 screen.
var terminalSizes = [][2]int{
	{80, 24}, {80, 25}, {60, 20}, {40, 15}, {30, 10},
	{100, 30}, {120, 40}, {200, 90}, {237, 120}, {300, 12}, {45, 200},
}

func TestNoLaunchFrameOverflowsItsTerminal(t *testing.T) {
	isolateGaiaHome(t)
	agent := catalog.NewCatalog().Get(catalog.FlagshipID)

	// The first frame, drawn before any size is known.
	m := root.NewFlagshipModel(*agent, false)
	assertFits(t, "pre-resize splash", m.View(), assumedCols, assumedRows)

	for _, size := range terminalSizes {
		w, h := size[0], size[1]
		m := root.NewFlagshipModel(*agent, false).
			WithLocalPreflight(preflight.LocalOptions{Binary: "gaia-agent-absent-fixture"})
		updated, _ := m.Update(tea.WindowSizeMsg{Width: w, Height: h})
		assertFits(t, "splash", updated.(root.FlagshipModel).View(), w, h)
	}
}

func TestNoChatFrameOverflowsItsTerminal(t *testing.T) {
	for _, size := range terminalSizes {
		w, h := size[0], size[1]
		m := chat.NewChatModelForFlagship(nil, "gaia", "GAIA", false, true)
		updated, _ := m.Update(tea.WindowSizeMsg{Width: w, Height: h})
		assertFits(t, "chat", updated.(chat.ChatModel).View(), w, h)
	}
}

// The readiness gate is the screen a user stares at longest, and the one whose
// height varies most — a halted row expands a whole remedy block underneath it,
// and `d` expands the raw probe output on top of that.
func TestNoPreflightFrameOverflowsItsTerminal(t *testing.T) {
	isolateGaiaHome(t)

	for _, size := range terminalSizes {
		w, h := size[0], size[1]
		d := newLocalDriver(t, "gaia-agent-absent-fixture", w, h)
		d.launch()
		if got := d.view(); got != "preflight" {
			t.Fatalf("%dx%d: view = %q, want preflight", w, h, got)
		}
		assertFits(t, "preflight", d.m.View(), w, h)

		d.send(key("d")) // details pane: the tallest the gate ever gets
		assertFits(t, "preflight with details", d.m.View(), w, h)
	}
}

// assertFits holds a rendered frame to the terminal it was laid out for, in
// BOTH dimensions: a line wider than the terminal soft-wraps, which costs a row
// and overflows the height by the back door.
func assertFits(t *testing.T, what, view string, cols, rows int) {
	t.Helper()
	lines := strings.Split(view, "\n")
	if len(lines) > rows {
		t.Errorf("%s at %dx%d rendered %d lines — %d too many:\n%s",
			what, cols, rows, len(lines), len(lines)-rows, ansi.Strip(view))
	}
	for i, line := range lines {
		if wide := ansi.StringWidth(line); wide > cols {
			t.Errorf("%s at %dx%d: line %d is %d columns (%d too wide): %q",
				what, cols, rows, i, wide, wide-cols, ansi.Strip(line))
		}
	}
}
