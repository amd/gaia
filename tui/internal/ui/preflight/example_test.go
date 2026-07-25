package preflight_test

import (
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// Example_mounting is the integration contract, compiled.
//
// It mirrors exactly what root.RootModel has to do to put the gate on the launch
// path: build it for the agent being launched, forward window size and messages
// to it, and act on the three messages it emits. If this stops compiling, the
// wiring in root/model.go is stale.
func Example_mounting() {
	agentID, agentName := "email", "Email"
	logf := func(string, ...any) {}

	gate := preflight.New(
		preflight.NewDaemonTransport(daemon.New(daemon.Options{Logf: logf})),
		preflight.ConfigFor(agentID, agentName),
		preflight.Options{Logf: logf},
	)

	// The host owns the screen; Init starts the probes.
	var cmd tea.Cmd = gate.Init()
	_ = cmd

	// Forwarding: the gate is a plain tea.Model.
	updated, _ := gate.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	gate = updated.(preflight.Model)
	_ = gate.View()

	// Acting on what it emits.
	handle := func(msg tea.Msg) string {
		switch msg := msg.(type) {
		case preflight.ProceedMsg:
			return "launch " + msg.AgentID
		case preflight.CancelMsg:
			return "back to the hub from " + msg.AgentID
		case preflight.ConnectMailboxMsg:
			return "open the connector flow for " + msg.Provider
		}
		return ""
	}
	_ = handle

	// Tearing it down must cancel whatever it started (a model pull outlives a
	// keypress).
	gate.Cancel()
}
