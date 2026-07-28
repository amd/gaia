package test

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/hub"
)

// 80x24 is the standard minimum terminal. The hub used to render 34 lines into
// it — a 10-line overflow with the footer wrapping mid-line.
func TestHubFitsAnEightyByTwentyFourTerminal(t *testing.T) {
	d := newDriver(t, nil, 80, 24)

	lines := viewLines(d.m)
	if len(lines) > 24 {
		t.Errorf("hub rendered %d lines into a 24-row terminal:\n%s", len(lines), plainView(d.m))
	}
	for i, line := range lines {
		if w := ansi.StringWidth(line); w > 80 {
			t.Errorf("line %d is %d columns wide (max 80): %q", i, w, line)
		}
	}
	if len(lines) < 10 {
		t.Errorf("hub collapsed to %d lines at 80x24 — the list has nowhere to render", len(lines))
	}

	// The wordmark replaces the 20-row robot, but the product still has to be
	// named and the keys still have to be visible.
	view := plainView(d.m)
	if !strings.Contains(view, "G A I A") {
		t.Error("compact header dropped the GAIA wordmark entirely")
	}
	if !strings.Contains(view, "q quit") {
		t.Errorf("footer is missing or truncated past the quit key at 80 columns:\n%s", view)
	}
}

// A tall terminal keeps the logo — the compact layout is a fallback, not a
// downgrade for everyone.
func TestHubKeepsTheLogoOnATallTerminal(t *testing.T) {
	d := newDriver(t, nil, 120, 44)
	lines := viewLines(d.m)
	if len(lines) > 44 {
		t.Errorf("hub rendered %d lines into a 44-row terminal", len(lines))
	}
	if len(lines) < 24 {
		t.Errorf("tall terminal rendered only %d lines — the logo is missing", len(lines))
	}
}

// #2481: tab, down, down, shift+tab used to leave nothing selected, so enter
// silently did nothing.
func TestTabSwitchResetsTheCursor(t *testing.T) {
	d := newDriver(t, nil, 120, 40)

	d.send(keyTab())
	d.send(keyDown())
	d.send(keyDown())
	if d.m.SelectedAgentID() == "" {
		t.Fatal("nothing selected after moving down on the second tab")
	}

	d.send(keyShiftTab())

	if got := d.m.SelectedAgentID(); got == "" {
		t.Fatal("#2481: nothing is selected after tab → down,down → shift+tab")
	}
	visible := d.m.VisibleAgentIDs()
	if len(visible) == 0 {
		t.Fatal("tab landed on an empty visible set")
	}
	if got := d.m.SelectedAgentID(); got != visible[0] {
		t.Errorf("cursor landed on %q, want the first row %q after a tab switch", got, visible[0])
	}
}

// Every tab must leave a usable selection, not just the pair the bug report used.
func TestEveryTabLeavesSomethingSelected(t *testing.T) {
	d := newDriver(t, nil, 120, 40)

	for i := 0; i < 6; i++ {
		visible := d.m.VisibleAgentIDs()
		_, name := d.m.ActiveTab()
		if len(visible) > 0 && d.m.SelectedAgentID() == "" {
			t.Fatalf("tab %q has %d rows but nothing selected", name, len(visible))
		}
		d.send(keyDown())
		d.send(keyDown())
		d.send(keyTab())
	}
}

// The `/` filter narrows the visible set the same way a tab switch does, and
// used to strand the cursor past the end of it.
func TestFilterKeepsTheCursorInsideTheVisibleSet(t *testing.T) {
	d := newDriver(t, nil, 120, 40)
	d.gotoTab("Available")

	// Move well down the unfiltered list, then filter to a single row.
	for i := 0; i < 5; i++ {
		d.send(keyDown())
	}
	d.send(key("/"))
	for _, r := range "docker" {
		d.send(key(string(r)))
	}
	d.send(keyEnter()) // apply the filter

	visible := d.m.VisibleAgentIDs()
	if len(visible) == 0 {
		t.Fatal("filtering for 'docker' matched nothing")
	}
	if got := d.m.SelectedAgentID(); got == "" {
		t.Fatalf("nothing selected after filtering to %d row(s)", len(visible))
	}
}

// A fresh machine must not open on a screen with nothing on it.
func TestHubOpensOnAPopulatedTab(t *testing.T) {
	cat := catalog.NewCatalog()
	// Simulate the fresh machine: nothing installed at all.
	for _, a := range cat.All() {
		if a.Status.IsLaunchable() {
			cat.SetStatus(a.ID, catalog.StatusAvailable)
		}
	}
	m := hub.NewHubModel(cat, nil, false)
	updated, _ := m.Update(windowSize(120, 40))
	m = updated.(hub.HubModel)

	if len(m.VisibleAgentIDs()) == 0 {
		_, name := m.ActiveTab()
		t.Fatalf("a fresh machine opens on the empty %q tab", name)
	}
	if m.SelectedAgentID() == "" {
		t.Fatal("first screen has no selected row")
	}
}

// An empty tab still has to tell the user what to do next.
func TestEmptyTabRendersAPointerNotJustNoItems(t *testing.T) {
	cat := catalog.NewCatalog()
	for _, a := range cat.All() {
		if a.Status.IsLaunchable() {
			cat.SetStatus(a.ID, catalog.StatusAvailable)
		}
	}
	m := hub.NewHubModel(cat, nil, false)
	updated, _ := m.Update(windowSize(120, 40))
	m = updated.(hub.HubModel)

	// Walk to the (now empty) Installed tab.
	for i := 0; i < 4; i++ {
		if _, name := m.ActiveTab(); name == "Installed" {
			break
		}
		next, _ := m.Update(keyTab())
		m = next.(hub.HubModel)
	}

	view := stripAnsi(m.View())
	if strings.Contains(view, "No items.") {
		t.Error("empty Installed tab still shows bubbles' bare 'No items.'")
	}
	if !strings.Contains(view, "No agents installed yet") {
		t.Errorf("empty Installed tab has no empty state:\n%s", view)
	}
	if !strings.Contains(view, "Available") {
		t.Errorf("empty state does not point at the Available tab:\n%s", view)
	}
}
