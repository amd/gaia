// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/x/ansi"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiaconnectors"
)

// connectorsPersonalizePrefix starts the optional personalization pass:
// `/connectors personalize <id>`. Dispatched before submit's switch because it
// takes a free-form argument.
const connectorsPersonalizePrefix = "/connectors personalize"

// connectorsRevokePrefix turns off the flagship's standing access to an MCP
// connector, which Personalize leaves on: `/connectors revoke <id>`.
const connectorsRevokePrefix = "/connectors revoke"

// connectorsActivatedMsg reports that the flagship may now use the connector
// a personalization pass is about to read.
type connectorsActivatedMsg struct {
	connector gaiaconnectors.Connector
	err       error
}

// activateConnectorCmd runs the CLI activation off the UI goroutine.
var activateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
	return func() tea.Msg {
		return connectorsActivatedMsg{connector: c, err: gaiaconnectors.Activate(context.Background(), c)}
	}
}

// connectorsRevokedMsg reports that the flagship can no longer use a connector.
type connectorsRevokedMsg struct {
	connector gaiaconnectors.Connector
	err       error
}

// deactivateConnectorCmd runs the CLI deactivation off the UI goroutine.
var deactivateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
	return func() tea.Msg {
		return connectorsRevokedMsg{connector: c, err: gaiaconnectors.Deactivate(context.Background(), c)}
	}
}

// connectorsListMsg carries `gaia connectors list --json`, and the connector
// to personalize or revoke when the list was fetched for that.
type connectorsListMsg struct {
	rows        []gaiaconnectors.Connector
	err         error
	personalize string
	revoke      string
}

// listConnectorsCmd runs the CLI probe off the UI goroutine.
var listConnectorsCmd = func(personalize, revoke string) tea.Cmd {
	return func() tea.Msg {
		rows, err := gaiaconnectors.List(context.Background())
		return connectorsListMsg{rows: rows, err: err, personalize: personalize, revoke: revoke}
	}
}

func isConnectorsPersonalize(query string) bool {
	return query == connectorsPersonalizePrefix || strings.HasPrefix(query, connectorsPersonalizePrefix+" ")
}

func isConnectorsRevoke(query string) bool {
	return query == connectorsRevokePrefix || strings.HasPrefix(query, connectorsRevokePrefix+" ")
}

func (m ChatModel) startConnectorsList() (tea.Model, tea.Cmd) {
	return m.statusNote("Checking connectors…"), listConnectorsCmd("", "")
}

func (m ChatModel) startConnectorsPersonalize(query string) (tea.Model, tea.Cmd) {
	name := strings.TrimSpace(strings.TrimPrefix(query, connectorsPersonalizePrefix))
	if name == "" {
		return m.statusNote("Name a connected connector: /connectors personalize <id>. " +
			"Type /connectors to see their ids."), nil
	}
	return m.statusNote("Checking " + name + "…"), listConnectorsCmd(name, "")
}

func (m ChatModel) startConnectorsRevoke(query string) (tea.Model, tea.Cmd) {
	name := strings.TrimSpace(strings.TrimPrefix(query, connectorsRevokePrefix))
	if name == "" {
		return m.statusNote("Name the connector to turn off GAIA's access to: /connectors revoke <id>. " +
			"Type /connectors to see which ones GAIA can use."), nil
	}
	return m.statusNote("Checking " + name + "…"), listConnectorsCmd("", name)
}

func (m ChatModel) handleConnectorsList(msg connectorsListMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf("Could not list connectors: %v", msg.err)), nil
	}
	if msg.revoke != "" {
		return m.handleConnectorsRevokeLookup(msg)
	}
	if msg.personalize == "" {
		return m.statusNote(gaiaconnectors.Render(msg.rows)), nil
	}
	c, ok := gaiaconnectors.Find(msg.rows, msg.personalize)
	if !ok {
		return m.statusNote(fmt.Sprintf("No connector named %q. Type /connectors to see their ids.",
			msg.personalize)), nil
	}
	if !c.Configured {
		return m.statusNote(fmt.Sprintf("%s isn't connected yet. Connect it from Connectors in the "+
			"Agent UI, then try again.", c.DisplayName)), nil
	}
	if !c.Usable() {
		return m.statusNote(fmt.Sprintf("%s is switched off. Turn it on under Connectors in the "+
			"Agent UI, then try again.", c.DisplayName)), nil
	}
	return m, activateConnectorCmd(c)
}

func (m ChatModel) handleConnectorsActivated(msg connectorsActivatedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote("Couldn't start personalization: " + msg.err.Error()), nil
	}
	// The probe ran in the background; a turn the user started meanwhile keeps
	// the floor, and the pass goes out when it ends.
	if m.streaming {
		return m, tea.Tick(slackOfferRetry, func(time.Time) tea.Msg { return msg })
	}
	if msg.connector.Type == "mcp_server" {
		m = m.statusNote(fmt.Sprintf("GAIA can now use %s, and keeps that access after this pass. "+
			"To turn it off: /connectors revoke %s", msg.connector.DisplayName, msg.connector.ID))
	}
	return m.sendQuery(gaiaconnectors.PersonalizePrompt(msg.connector))
}

func (m ChatModel) handleConnectorsRevokeLookup(msg connectorsListMsg) (tea.Model, tea.Cmd) {
	c, ok := gaiaconnectors.Find(msg.rows, msg.revoke)
	if !ok {
		return m.statusNote(fmt.Sprintf("No connector named %q. Type /connectors to see their ids.",
			msg.revoke)), nil
	}
	if c.Type != "mcp_server" {
		return m.statusNote(fmt.Sprintf("GAIA's access to %s comes with connecting it. Disconnect it "+
			"under Connectors in the Agent UI to remove that.", c.DisplayName)), nil
	}
	// An older CLI reports no activations; deactivating is idempotent, so try.
	if c.Activations != nil && !c.FlagshipCanUse() {
		return m.statusNote(fmt.Sprintf("GAIA already can't use %s.", c.DisplayName)), nil
	}
	return m, deactivateConnectorCmd(c)
}

func (m ChatModel) handleConnectorsRevoked(msg connectorsRevokedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote("Couldn't turn off GAIA's access: " + msg.err.Error()), nil
	}
	return m.statusNote(fmt.Sprintf("GAIA can no longer use %s. Turn it back on under Connectors "+
		"in the Agent UI.", msg.connector.DisplayName)), nil
}

// connectorsHint is the status bar's Connectors entry; clicking it runs /connectors.
const connectorsHint = "/connectors"

func (m ChatModel) connectorsHintShown() bool {
	return !m.streaming && m.confirmation == nil && !m.warming && !m.palette.open
}

// clickedConnectorsHint reports whether (x, y) lands on the status bar's
// Connectors entry. The bar is the last row of the screen.
func (m ChatModel) clickedConnectorsHint(x, y int) bool {
	if !m.connectorsHintShown() || y != m.height-1 {
		return false
	}
	plain := ansi.Strip(m.renderStatusBar())
	i := strings.Index(plain, connectorsHint)
	if i < 0 {
		return false
	}
	col := ansi.StringWidth(plain[:i])
	return x >= col && x < col+len(connectorsHint)
}
