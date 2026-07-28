package test

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// The failure this whole change exists to stop: the hub opened a chat for an
// agent whose binary was never built, painted "Connected to: Bash", and only
// failed when the user sent their first message with
// `exec: "gaia-bash": executable file not found in $PATH`.
//
// A launch must verify the agent can start BEFORE any screen claims it did.
func TestLaunchingAnAgentWithNoBinaryStaysInTheHubAndSaysWhy(t *testing.T) {
	cat := catalog.NewCatalog()
	m := root.NewRootModelWithHub(cat, nil, false)
	updated, _ := m.Update(windowSize(120, 40))
	m = updated.(root.RootModel)

	// A launchable row whose binary resolves nowhere — exactly the state the
	// seed catalog used to ship in.
	ghost := catalog.Agent{
		ID: "bash", Name: "Bash", Status: catalog.StatusInstalled,
		Transport: catalog.TransportSubprocess, BinaryPath: "gaia-bash-that-was-never-built",
	}
	updated, _ = m.Update(hub.LaunchAgentMsg{Agent: ghost})
	m = updated.(root.RootModel)

	snap := m.ControlSnapshot()
	if snap.View == "chat" {
		t.Fatal("the hub opened a chat for an agent that cannot start; the failure would only " +
			"appear on the first message")
	}
	if snap.View != "hub" {
		t.Fatalf("view = %q, want hub", snap.View)
	}

	view := stripAnsi(m.View())
	if !strings.Contains(view, "cannot start") {
		t.Errorf("the hub does not say the launch failed:\n%s", view)
	}
}

// The other half: an agent whose binary IS there still launches. A check that
// refuses everything would be no better than one that refuses nothing.
func TestLaunchingAnAgentWithARealBinaryOpensChat(t *testing.T) {
	cat := catalog.NewCatalog()
	cat.SetMockBinary(mockBinaryPath(t))
	m := root.NewRootModelWithHub(cat, nil, false)
	updated, _ := m.Update(windowSize(120, 40))
	m = updated.(root.RootModel)

	updated, _ = m.Update(hub.LaunchAgentMsg{Agent: *cat.Get("bash")})
	m = updated.(root.RootModel)

	if snap := m.ControlSnapshot(); snap.View != "chat" {
		t.Fatalf("a launchable agent with a real binary did not open chat (view=%q)", snap.View)
	}
}

// Coming Soon is not a launch: pressing enter on one must explain, not connect.
func TestEnterOnAComingSoonAgentDoesNotLaunch(t *testing.T) {
	d := newDriver(t, nil, 120, 40)
	d.gotoTab("Coming Soon")
	d.selectAgent("bash")
	d.send(keyEnter())

	view := plainView(d.m)
	if !strings.Contains(view, "not published") {
		t.Errorf("enter on a Coming Soon agent said nothing useful:\n%s", view)
	}
}

// `i` on an agent the Agent Hub does not publish must refuse with the reason —
// the daemon has no spec for it and the install would 404.
func TestInstallOnAnUnpublishedAgentRefusesWithAReason(t *testing.T) {
	d, _ := newHubOnFakeDaemon(t)
	d.gotoTab("Coming Soon")
	d.selectAgent("bash")
	d.send(key("i"))

	view := plainView(d.m)
	if !strings.Contains(view, "cannot be installed") {
		t.Errorf("pressing i on an unpublished agent did not refuse clearly:\n%s", view)
	}
	if d.m.Overlay() != "" {
		t.Errorf("an install started for an agent the daemon cannot fetch (overlay=%q)", d.m.Overlay())
	}
}
