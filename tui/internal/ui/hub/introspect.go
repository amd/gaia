package hub

import (
	"github.com/charmbracelet/bubbles/list"
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
)

// OwnsMsg reports whether msg is one of the hub's own asynchronous results.
//
// The root model routes these to the hub even when the chat view is on screen.
// They are answers to work the hub started (a catalog fetch, an install poll)
// and dropping one strands that work: a catalog load that resolved while the
// user was in chat used to leave the hub stuck on "loading list" for the rest
// of the session.
func OwnsMsg(msg tea.Msg) bool {
	switch msg.(type) {
	case catalogLoadedMsg, catalogFailedMsg,
		installQueuedMsg, installProgressMsg, installFailedMsg, installTrustRequiredMsg,
		trustDecisionMsg, uninstallDoneMsg, uninstallFailedMsg:
		return true
	}
	return false
}

// ActiveTab returns the index and label of the selected category tab.
func (m HubModel) ActiveTab() (int, string) {
	if m.activeTab < 0 || m.activeTab >= len(m.tabs) {
		return m.activeTab, ""
	}
	return m.activeTab, string(m.tabs[m.activeTab])
}

// SelectedAgentID is the catalog id of the highlighted row, or "" when the list
// is empty.
func (m HubModel) SelectedAgentID() string {
	agent, ok := m.list.SelectedItem().(catalog.Agent)
	if !ok {
		return ""
	}
	return agent.ID
}

// VisibleAgentIDs lists the rows currently on screen, in order, honouring any
// active filter. Automation walks this to move the selection deterministically
// instead of guessing how many times to press down.
func (m HubModel) VisibleAgentIDs() []string {
	items := m.list.VisibleItems()
	ids := make([]string, 0, len(items))
	for _, item := range items {
		if agent, ok := item.(catalog.Agent); ok {
			ids = append(ids, agent.ID)
		}
	}
	return ids
}

// IsFiltering reports whether a search filter is being typed or is applied —
// either way the visible rows are a subset, so a caller must clear it before
// counting rows.
func (m HubModel) IsFiltering() bool {
	return m.list.FilterState() != list.Unfiltered
}

// Overlay names the modal currently covering the hub, or "" when there is none.
func (m HubModel) Overlay() string {
	switch {
	case m.trust != nil:
		return "trust"
	case m.install != nil:
		return "install"
	case m.confirm != nil:
		return "confirm"
	}
	return ""
}

// InstallState reports the id and status of the install the hub is showing, or
// ("", "") when no install box is up. Automation waits on this instead of
// grepping the rendered progress bar.
func (m HubModel) InstallState() (agentID, status string) {
	if m.install == nil {
		return "", ""
	}
	return m.install.agentID, m.install.status
}
