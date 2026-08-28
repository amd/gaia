package root

import (
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// ControlSnapshot reports where the user currently is, for the control API's
// /status and for POST /wait state matchers.
//
// It satisfies control.SnapshotProvider. Reporting real model state — rather
// than inferring it from the rendered characters — is what lets an assistant
// wait on "the chat view for agent X is open" instead of racing on a substring.
func (m FlagshipModel) ControlSnapshot() control.Snapshot {
	snap := control.Snapshot{View: control.ViewUnknown, Agent: m.agent.ID}

	switch m.activeView {
	case viewSplash:
		snap.View = control.ViewSplash
	case viewPreflight:
		snap.View = control.ViewPreflight
		if m.preflight != nil {
			snap.Agent = m.preflight.AgentID()
			// The row refusing the launch, by key. Automation asserts on this
			// instead of grepping the rendered remedy for a phrase.
			if blocker, blocked := m.preflight.Report().Blocker(); blocked {
				snap.Blocker = blocker.Key
			}
		}
		if m.connect != nil {
			snap.Overlay = "connect-mailbox"
		}
	case viewChat:
		snap.View = control.ViewChat
		if m.chat != nil {
			snap.Agent = m.chat.AgentID()
			snap.Streaming = m.chat.IsStreaming()
		}
	}

	if m.help.Open {
		snap.Overlay = "help"
	}
	// Halt wins over every other overlay. It draws nothing and intercepts no
	// key — the screen that raised it (preflight.Model) already pauses
	// itself and explains why — but automation still needs a signal, and
	// this is it: the underlying View stays whatever it already was, so
	// /status never blinds to "unknown" while held.
	if m.Halted() {
		snap.Overlay = "halt"
	}
	return snap
}

// PreflightReport is the gate's current answer, and false when no gate is on
// screen. It exists so a test can assert on ROWS — which row refused, in what
// state — instead of grepping the rendered remedy for a phrase that is allowed
// to change.
func (m FlagshipModel) PreflightReport() (preflight.Report, bool) {
	if m.activeView != viewPreflight || m.preflight == nil {
		return preflight.Report{}, false
	}
	return m.preflight.Report(), true
}

// GateAskedAboutSetupForTest exposes gateAskedAboutSetup to the integration
// test package, which drives the real report the real runner produces rather
// than a hand-built one.
func GateAskedAboutSetupForTest(rep preflight.Report) bool { return gateAskedAboutSetup(rep) }
