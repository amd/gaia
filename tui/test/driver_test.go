package test

import (
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/hub"
)

// maxPumpSteps bounds the command loop so a bug that keeps re-issuing a command
// fails the test instead of hanging it.
const maxPumpSteps = 200

// driver runs a HubModel the way Bubble Tea does: send a message, execute the
// command it returns, feed the resulting message back.
type driver struct {
	t   *testing.T
	m   hub.HubModel
	cat *catalog.Catalog
}

func newDriver(t *testing.T, hc *catalog.HubClient, width, height int) *driver {
	t.Helper()
	cat := catalog.NewCatalog()
	d := &driver{t: t, m: hub.NewHubModel(cat, hc, false), cat: cat}
	d.send(tea.WindowSizeMsg{Width: width, Height: height})
	return d
}

// send delivers one message and runs every command it produces to completion.
func (d *driver) send(msg tea.Msg) {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(hub.HubModel)
	d.pump(cmd)
}

// sendNoPump delivers one message and returns the command WITHOUT running it.
// Used where the point of the test is that a command was (or was not) issued.
func (d *driver) sendNoPump(msg tea.Msg) tea.Cmd {
	d.t.Helper()
	updated, cmd := d.m.Update(msg)
	d.m = updated.(hub.HubModel)
	return cmd
}

func (d *driver) pump(cmd tea.Cmd) {
	d.t.Helper()
	queue := []tea.Cmd{cmd}
	for steps := 0; len(queue) > 0; steps++ {
		if steps > maxPumpSteps {
			d.t.Fatalf("command loop did not settle after %d steps", maxPumpSteps)
		}
		next := queue[0]
		queue = queue[1:]
		if next == nil {
			continue
		}
		msg := next()
		if msg == nil {
			continue
		}
		if batch, ok := msg.(tea.BatchMsg); ok {
			queue = append(queue, batch...)
			continue
		}
		if isCursorBlink(msg) {
			// The search input's cursor re-arms its own blink forever. Real
			// Bubble Tea just keeps ticking; a test loop must not.
			continue
		}
		updated, follow := d.m.Update(msg)
		d.m = updated.(hub.HubModel)
		queue = append(queue, follow)
	}
}

// isCursorBlink matches bubbles/cursor's blink messages. One of the two types
// is unexported, so this matches on the type name rather than skipping the
// whole cursor package.
func isCursorBlink(msg tea.Msg) bool {
	name := fmt.Sprintf("%T", msg)
	return strings.HasPrefix(name, "cursor.") && strings.Contains(name, "BlinkMsg")
}

// key builds a KeyMsg for a printable key. Named keys (tab, esc, enter) have
// their own constructors below — sending them as runes is the bug this file
// exists to stop repeating.
func key(s string) tea.KeyMsg {
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func keyTab() tea.KeyMsg       { return tea.KeyMsg{Type: tea.KeyTab} }
func keyShiftTab() tea.KeyMsg  { return tea.KeyMsg{Type: tea.KeyShiftTab} }
func keyDown() tea.KeyMsg      { return tea.KeyMsg{Type: tea.KeyDown} }
func keyEnter() tea.KeyMsg     { return tea.KeyMsg{Type: tea.KeyEnter} }
func keyEsc() tea.KeyMsg       { return tea.KeyMsg{Type: tea.KeyEscape} }
func keyBackspace() tea.KeyMsg { return tea.KeyMsg{Type: tea.KeyBackspace} }

// selectAgent moves the cursor onto agentID within the active tab.
func (d *driver) selectAgent(agentID string) {
	d.t.Helper()
	for i, id := range d.m.VisibleAgentIDs() {
		if id != agentID {
			continue
		}
		for j := 0; j < i; j++ {
			d.send(keyDown())
		}
		if got := d.m.SelectedAgentID(); got != agentID {
			d.t.Fatalf("selectAgent(%q) landed on %q", agentID, got)
		}
		return
	}
	d.t.Fatalf("agent %q is not visible on tab %q (visible: %v)",
		agentID, tabName(d.m), d.m.VisibleAgentIDs())
}

// gotoTab presses tab until the named section is active.
func (d *driver) gotoTab(name string) {
	d.t.Helper()
	for i := 0; i < 6; i++ {
		if tabName(d.m) == name {
			return
		}
		d.send(keyTab())
	}
	d.t.Fatalf("never reached tab %q", name)
}

func tabName(m hub.HubModel) string {
	_, name := m.ActiveTab()
	return name
}

func plainView(m hub.HubModel) string { return stripAnsi(m.View()) }

func viewLines(m hub.HubModel) []string { return strings.Split(plainView(m), "\n") }
