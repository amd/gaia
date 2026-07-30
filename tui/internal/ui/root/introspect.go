package root

import "github.com/amd/gaia/tui/internal/control"

// ControlSnapshot reports where the user currently is, for the control API's
// /status and for POST /wait state matchers.
//
// It satisfies control.SnapshotProvider. Reporting real model state — rather
// than inferring it from the rendered characters — is what lets an assistant
// wait on "the chat view for agent X is open" instead of racing on a substring.
func (m RootModel) ControlSnapshot() control.Snapshot {
	snap := control.Snapshot{View: control.ViewUnknown, VisibleAgentIDs: []string{}}

	switch m.activeView {
	case viewHub:
		snap.View = "hub"
		snap.HubTabIndex, snap.HubTab = m.hub.ActiveTab()
		snap.SelectedAgentID = m.hub.SelectedAgentID()
		snap.VisibleAgentIDs = m.hub.VisibleAgentIDs()
		snap.Filtering = m.hub.IsFiltering()
		snap.Overlay = m.hub.Overlay()
	case viewPreflight:
		snap.View = "preflight"
		if m.preflight != nil {
			snap.Agent = m.preflight.AgentID()
		}
		if m.connect != nil {
			snap.Overlay = "connect-mailbox"
		}
	case viewChat:
		snap.View = "chat"
		if m.chat != nil {
			snap.Agent = m.chat.AgentID()
			snap.Streaming = m.chat.IsStreaming()
			snap.CanReturnToHub = m.chat.CanReturnToHub()
		}
	}

	if m.showHelp {
		snap.Overlay = "help"
	}
	// Halt wins over every other overlay — same precedence as View() and the
	// keyboard ownership in Update. The underlying View stays whatever it
	// already was, so /status never blinds to "unknown" while held.
	if m.Halted() {
		snap.Overlay = "halt"
	}
	return snap
}
