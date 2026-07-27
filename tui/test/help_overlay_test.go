package test

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/components"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// The help overlay grew when the install keys landed. It has to keep fitting
// the minimum terminal, or `?` becomes another way to overflow the screen.
func TestHelpOverlayFitsEightyByTwentyFour(t *testing.T) {
	for _, ctx := range []components.HelpContext{
		components.HelpContextHub,
		components.HelpContextChat,
	} {
		rendered := components.RenderHelpOverlay(ctx, "", 80, 24)
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

// `?` documents the keys the hub actually binds. A help screen that lists a key
// the model does not handle is worse than none.
func TestHubHelpDocumentsTheInstallKeys(t *testing.T) {
	rendered := stripAnsi(components.RenderHelpOverlay(components.HelpContextHub, "", 100, 40))
	for _, want := range []string{"Install", "Uninstall", "Refresh", "non-verified"} {
		if !strings.Contains(rendered, want) {
			t.Errorf("hub help does not mention %q:\n%s", want, rendered)
		}
	}
	if strings.Contains(rendered, "Request a new agent") {
		t.Error("hub help still binds r to 'request an agent'; it now refreshes the catalog")
	}
}

// Pressing ? in the hub must actually reach the overlay through the root model.
func TestQuestionMarkOpensTheHubHelp(t *testing.T) {
	cat := catalog.NewCatalog()
	m := root.NewRootModelWithHub(cat, nil, false)

	updated, _ := m.Update(windowSize(100, 40))
	m = updated.(root.RootModel)

	updated, cmd := m.Update(key("?"))
	m = updated.(root.RootModel)
	if cmd != nil {
		if msg := cmd(); msg != nil {
			updated, _ = m.Update(msg)
			m = updated.(root.RootModel)
		}
	}

	if snap := m.ControlSnapshot(); snap.Overlay != "help" {
		t.Fatalf("overlay after ? = %q, want help", snap.Overlay)
	}
	if !strings.Contains(stripAnsi(m.View()), "Install it") {
		t.Error("the help overlay is not what got rendered")
	}
}
