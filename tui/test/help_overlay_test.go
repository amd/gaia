package test

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// The help overlay grew when the install keys landed. It has to keep fitting
// the minimum terminal — however much of the panel that leaves visible, the
// rendered box itself is never allowed to overflow 24 rows, or `?` becomes
// another way to overflow the screen.
func TestHelpOverlayFitsEightyByTwentyFour(t *testing.T) {
	for _, ctx := range []components.HelpContext{components.HelpContextChat} {
		rendered := components.RenderHelpOverlay(ctx, "", 80, 24, 0)
		lines := strings.Split(stripAnsi(rendered), "\n")
		if len(lines) > 24 {
			t.Errorf("help context %d renders %d lines into 24 rows:\n%s", ctx, len(lines), rendered)
		}
		for i, line := range lines {
			if w := ansi.StringWidth(line); w > 80 {
				t.Errorf("help context %d line %d is %d columns wide", ctx, i, w)
			}
		}
	}
}

// Once the panel can be longer than the box, the keys that used to always
// close it (↑/↓/PgUp/PgDn/Home/End) have to navigate it instead — otherwise
// every future line just makes scrolling worse, not possible. This drives
// the actual root-model key routing, not just the renderer underneath it.
func TestHelpNavigationKeysScrollWithoutClosingTheOverlay(t *testing.T) {
	m := newFlagshipForHelp(t)

	// Short enough that chatHelpText overflows the box and the panel has to
	// scroll — see TestHelpScrollReachesTheLastLineAndTheIndicatorAgrees in
	// the components package for the same overflow at the renderer layer.
	updated, _ := m.Update(windowSize(80, 10))
	m = updated.(root.FlagshipModel)

	updated, cmd := m.Update(chat.ToggleHelpMsg{})
	m = updated.(root.FlagshipModel)
	if cmd != nil {
		t.Fatal("opening help returned a command; nothing should fire on open")
	}
	if snap := m.ControlSnapshot(); snap.Overlay != "help" {
		t.Fatalf("overlay after opening help = %q, want help", snap.Overlay)
	}

	top := stripAnsi(m.View())
	if !strings.Contains(top, "more below") {
		t.Fatalf("freshly opened help doesn't say there's more below at 80x10:\n%s", top)
	}

	navKeys := []tea.KeyMsg{
		{Type: tea.KeyDown}, {Type: tea.KeyDown}, {Type: tea.KeyPgDown}, {Type: tea.KeyEnd},
	}
	for _, k := range navKeys {
		updated, cmd = m.Update(k)
		m = updated.(root.FlagshipModel)
		if cmd != nil {
			t.Fatalf("navigating help returned a command for %v; it should only move the scroll offset", k.Type)
		}
		if snap := m.ControlSnapshot(); snap.Overlay != "help" {
			t.Fatalf("overlay closed on a navigation key (%v) instead of scrolling", k.Type)
		}
	}

	bottom := stripAnsi(m.View())
	if !strings.Contains(bottom, "more above") {
		t.Fatalf("End did not scroll chat help to the bottom at 80x10:\n%s", bottom)
	}
	if strings.Contains(bottom, "more below") {
		t.Fatalf("End left the panel claiming there's more below when it's already at the end:\n%s", bottom)
	}

	// Home has to reach back to the top just as reliably.
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyHome})
	m = updated.(root.FlagshipModel)
	if cmd != nil {
		t.Fatal("Home returned a command; it should only move the scroll offset")
	}
	afterHome := stripAnsi(m.View())
	if !strings.Contains(afterHome, "more below") || strings.Contains(afterHome, "more above") {
		t.Fatalf("Home did not scroll chat help back to the top:\n%s", afterHome)
	}

	// Any other key still closes the panel, exactly like every key did before
	// it could scroll at all.
	updated, cmd = m.Update(key("x"))
	m = updated.(root.FlagshipModel)
	if cmd != nil {
		t.Fatal("closing help returned a command; nothing should fire on close")
	}
	if snap := m.ControlSnapshot(); snap.Overlay == "help" {
		t.Fatal("a non-navigation key did not close the open help panel")
	}
}

// Esc has always closed the help overlay; scrolling must not change that.
// Root intercepts every key while help is open and returns before the
// underlying chat model ever sees the KeyMsg, so Esc here can neither quit
// the app nor cancel whatever turn the screen underneath was running.
func TestHelpEscClosesWithoutQuittingOrForwarding(t *testing.T) {
	m := newFlagshipForHelp(t)

	updated, _ := m.Update(windowSize(80, 24))
	m = updated.(root.FlagshipModel)

	updated, _ = m.Update(chat.ToggleHelpMsg{})
	m = updated.(root.FlagshipModel)
	if snap := m.ControlSnapshot(); snap.Overlay != "help" {
		t.Fatalf("overlay after opening help = %q, want help", snap.Overlay)
	}

	updated, cmd := m.Update(keyEsc())
	m = updated.(root.FlagshipModel)
	if cmd != nil {
		t.Fatal("Esc while help is open returned a command — it must only close the overlay")
	}
	if snap := m.ControlSnapshot(); snap.Overlay == "help" {
		t.Fatal("Esc did not close the help overlay")
	}
}

// newFlagshipForHelp builds the launch router without starting it, so the help
// overlay can be driven without a readiness probe running underneath it. The
// overlay is owned by the router and works from whatever view is on screen.
func newFlagshipForHelp(t *testing.T) root.FlagshipModel {
	t.Helper()
	agent := catalog.NewCatalog().Get(catalog.FlagshipID)
	if agent == nil {
		t.Fatalf("the catalog has no %q entry", catalog.FlagshipID)
	}
	return root.NewFlagshipModel(*agent, false)
}
