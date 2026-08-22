package test

import (
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/hub"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// A modal that swallows ctrl+c leaves the user with no way out of a full-screen
// terminal app.
func TestCtrlCQuitsFromEveryModal(t *testing.T) {
	cases := []struct {
		name string
		open func(*driver)
	}{
		{"trust gate", func(d *driver) {
			d.gotoTab("Available")
			d.selectAgent("email")
			d.send(key("i"))
		}},
		{"install progress", func(d *driver) {
			d.gotoTab("Available")
			d.selectAgent("email")
			d.send(key("i"))
			d.send(key("y"))
		}},
		{"uninstall confirm", func(d *driver) {
			// Installed has to be reached by installing something: no agent
			// ships installed any more.
			d.gotoTab("Available")
			d.selectAgent("email")
			d.send(key("i"))
			d.send(key("y"))
			d.send(keyEsc()) // dismiss the install result box
			d.gotoTab("Installed")
			d.selectAgent("email")
			d.send(key("d"))
		}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d, _ := newHubOnFakeDaemon(t)
			tc.open(d)
			if d.m.Overlay() == "" {
				t.Fatalf("%s did not open", tc.name)
			}

			cmd := d.sendNoPump(tea.KeyMsg{Type: tea.KeyCtrlC})
			if cmd == nil {
				t.Fatal("ctrl+c returned no command — the modal swallowed the quit")
			}
			if _, ok := cmd().(tea.QuitMsg); !ok {
				t.Fatalf("ctrl+c produced %T, want tea.QuitMsg", cmd())
			}
		})
	}
}

// Two `y` presses inside one frame must not produce two installs: the daemon
// 409s the duplicate and the UI would paint a failure over a succeeding
// install.
func TestDoubleConfirmSendsOneTrustedInstall(t *testing.T) {
	d, fake := newHubOnFakeDaemon(t)
	d.gotoTab("Available")
	d.selectAgent("email")

	d.send(key("i")) // 403 → trust gate
	before := fake.installCallCount()

	// Both presses are delivered before either command resolves.
	first := d.sendNoPump(key("y"))
	second := d.sendNoPump(key("y"))
	d.pump(first)
	d.pump(second)

	trusted := 0
	for i := before; i < fake.installCallCount(); i++ {
		if fake.installBody(i)["trusted"] == true {
			trusted++
		}
	}
	if trusted != 1 {
		t.Fatalf("a double confirm sent %d trusted installs, want exactly 1", trusted)
	}
}

// The trust gate must not honour an approval when no gate is up. Nothing can
// build the message today except a keypress, but the gate's integrity must not
// depend on that staying true.
func TestApprovalWithNoGateOpenInstallsNothing(t *testing.T) {
	d, fake := newHubOnFakeDaemon(t)
	d.gotoTab("Available")
	d.selectAgent("email")

	// No `i` first: nothing is pending.
	d.send(key("y"))

	if got := fake.installCallCount(); got != 0 {
		t.Fatalf("an unsolicited approval produced %d install calls, want 0", got)
	}
}

// The 80x24 test that shipped with the layout fix used the seed catalog only,
// where no row is long enough to widen the frame. With the hub catalog merged,
// the "not out" rows are — and lipgloss pads every block to the widest one, so
// one long row wraps the whole screen.
func TestHubFitsEightyByTwentyFourOnEveryTabWithTheHubCatalog(t *testing.T) {
	fake := newFakeDaemon(t)
	// Nothing bounds how long a published description is, and one over-long
	// row widens every other row with it.
	body := emailCatalog(false)
	body["agents"] = append(body["agents"].([]map[string]any), map[string]any{
		"id":   "verbose",
		"name": "An Agent With A Really Rather Long Display Name",
		"description": "A description long enough to run past eighty columns on its own, " +
			"which a published catalog entry is perfectly entitled to carry.",
		"category":            "a category name that is also unreasonably long, as it happens",
		"security_tier":       "experimental",
		"latest_version":      "1.0.0",
		"download_size_bytes": 12345678,
		"supervised":          true,
		"installed":           false,
	})
	fake.catalogBody = body

	d := newDriver(t, fake.client(), 80, 24)
	d.pump(d.m.Init())

	for _, want := range []string{"Installed", "Available", "Coming Soon"} {
		d.gotoTab(want)
		lines := viewLines(d.m)
		if len(lines) > 24 {
			t.Errorf("tab %q rendered %d lines into 24 rows:\n%s", want, len(lines), plainView(d.m))
		}
		for i, line := range lines {
			if w := ansi.StringWidth(line); w > 80 {
				t.Errorf("tab %q line %d is %d columns wide (max 80): %q", want, i, w, line)
			}
		}
	}
}

// A daemon launch failure quotes its launcher output, which is multi-line.
// Truncating to the width does not collapse newlines, so the status row would
// silently blow the one-line budget chromeHeight reserves for it.
func TestMultiLineStatusStaysOneRow(t *testing.T) {
	d := newDriver(t, nil, 80, 24)
	d.m.SetStatus("failed to start the daemon: exited with 1. Launcher output:\nline two\nline three\nline four")

	lines := viewLines(d.m)
	if len(lines) > 24 {
		t.Fatalf("a multi-line status rendered %d lines into 24 rows:\n%s", len(lines), plainView(d.m))
	}
	if !strings.Contains(plainView(d.m), "failed to start the daemon") {
		t.Error("the status was dropped rather than clipped")
	}
}

// A catalog load that resolves while the user is in chat used to be discarded
// by the root model, leaving the hub stuck on "loading list" forever.
func TestHubCatalogLoadSurvivesTheChatView(t *testing.T) {
	_, binDir := buildBinaries(t)
	fake := newFakeDaemon(t)

	cat := catalog.NewCatalog()
	// buildBinaries names the mock with the platform suffix; without it the
	// launch cannot resolve the binary on Windows and never leaves the hub.
	mock := filepath.Join(binDir, "gaia-bash")
	if runtime.GOOS == "windows" {
		mock += ".exe"
	}
	cat.SetMockBinary(mock)
	m := root.NewRootModelWithHub(cat, fake.client(), false)

	updated, _ := m.Update(windowSize(120, 40))
	m = updated.(root.RootModel)
	pending := m.Init() // the catalog fetch, not yet resolved

	// Switch to chat before it lands.
	updated, _ = m.Update(hub.LaunchAgentMsg{Agent: *cat.Get("bash")})
	m = updated.(root.RootModel)
	if snap := m.ControlSnapshot(); snap.View != "chat" {
		t.Fatalf("did not reach the chat view (view=%q)", snap.View)
	}

	// Now the fetch resolves.
	if msg := pending(); msg != nil {
		updated, _ = m.Update(msg)
		m = updated.(root.RootModel)
	}

	// Back to the hub: the list must be loaded, not stuck.
	updated, _ = m.Update(chat.ReturnToHubMsg{AgentID: "bash"})
	m = updated.(root.RootModel)

	view := stripAnsi(m.View())
	if strings.Contains(view, "loading list") {
		t.Errorf("the hub is still loading after its catalog resolved in the chat view:\n%s", view)
	}
	if cat.Get("email").NotOfferedReason != "" || !cat.Get("email").Installable() {
		t.Errorf("the hub catalog was never applied: %+v", cat.Get("email"))
	}
}
