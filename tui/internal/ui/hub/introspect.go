package hub

import (
	"github.com/charmbracelet/bubbles/list"

	"github.com/amd/gaia/tui/internal/catalog"
)

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
	if m.confirm != nil {
		return "confirm"
	}
	return ""
}
