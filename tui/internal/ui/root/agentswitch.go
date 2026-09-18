package root

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/agents"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// WithCatalog gives the /agents switch path something to resolve a picked id
// against: the full catalog.Agent record (transport, binary path, version) —
// not just the runtime rows the hub-agents panel itself lists over the wire.
func (m FlagshipModel) WithCatalog(cat *catalog.Catalog) FlagshipModel {
	m.catalog = cat
	return m
}

// WithHubClient overrides the client that feeds the /agents panel. Tests
// stub agents.HubAgentLister so listing installed agents never talks to a
// real daemon; a real session leaves this alone and gets hub()'s lazily
// built one.
func (m FlagshipModel) WithHubClient(hc agents.HubAgentLister) FlagshipModel {
	m.hubClient = hc
	return m
}

// hub returns the /agents panel's data source, building it on first use —
// the same lazy-pointer pattern as preflightTransport, so a session that
// never opens the panel never constructs a daemon client for it.
func (m *FlagshipModel) hub() agents.HubAgentLister {
	if m.hubClient == nil {
		m.hubClient = catalog.NewHubClient(m.logf)
	}
	return m.hubClient
}

// switchAgent begins an in-place switch to the agent picked from /agents,
// re-entering the readiness gate rather than trusting the current session's
// old pass — see beginPreflight's own doc comment for why nothing here is
// remembered from the last check.
func (m FlagshipModel) switchAgent(id string) (tea.Model, tea.Cmd) {
	if m.agent.ID == id {
		if m.chat != nil {
			m.chat.AppendStatus(fmt.Sprintf("Already running %s.", id))
		}
		return m, nil
	}

	var next *catalog.Agent
	if m.catalog != nil {
		next = m.catalog.Get(id)
		if next == nil {
			// The panel that offered id came from the daemon's live catalog; an
			// install completed after this session's own catalog last scanned
			// disk (including one run from /agents earlier in the same
			// session) would otherwise be reported back as not installed.
			m.catalog.LoadInstalledAgents()
			next = m.catalog.Get(id)
		}
	}
	if next == nil {
		if m.chat != nil {
			m.chat.AppendStatus(fmt.Sprintf(
				"%q is not an installed agent GAIA knows about. Run `gaia hub install %s`, then try /agents again.",
				id, id))
		}
		return m, nil
	}

	// The outgoing client is NOT touched here: the user can still back out of
	// this gate (cancelFromGate) and land back on the live session, and a
	// cancelled turn or a closed connection cannot be undone. launchAgent
	// closes it, and only once the gate actually passes.
	var carried []chat.Message
	if m.chat != nil {
		carried = m.chat.Messages()
	}

	m.pendingTranscript = append(carried, chat.Message{
		Role:    chat.RoleStatus,
		Content: switchDivider(m.agent, *next),
	})
	return m.beginPreflight(*next)
}

// switchDivider is the line the switched-to transcript gets appended with,
// so the new agent's ignorance of everything above it is never left implicit.
func switchDivider(from, to catalog.Agent) string {
	return fmt.Sprintf("Switched from %s to %s — %s does not see the conversation above this line.",
		from.ID, to.ID, to.ID)
}
