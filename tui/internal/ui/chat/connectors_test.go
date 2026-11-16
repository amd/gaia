// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/gaiaconnectors"
)

var testConnectors = []gaiaconnectors.Connector{
	{ID: "google", DisplayName: "Google", Configured: true, AccountID: "me@example.com"},
	{ID: "mcp-slack", DisplayName: "Slack"},
}

func TestConnectorsCommandListsConnectors(t *testing.T) {
	m := slackModel(t)
	updated, cmd := m.submit("/connectors")
	if cmd == nil {
		t.Fatal("/connectors must start the CLI probe")
	}
	updated, _ = updated.(ChatModel).handleConnectorsList(connectorsListMsg{rows: testConnectors})
	text := lastStatus(t, updated.(ChatModel))
	for _, want := range []string{"Google", "connected as me@example.com", "Slack", "not connected", "/connectors personalize"} {
		if !strings.Contains(text, want) {
			t.Errorf("listing missing %q:\n%s", want, text)
		}
	}
}

func TestPersonalizeActivatesThenSendsThePrompt(t *testing.T) {
	m := slackModel(t)
	before := len(m.messages)
	updated, cmd := m.handleConnectorsList(connectorsListMsg{rows: testConnectors, personalize: "google"})
	if cmd == nil || len(updated.(ChatModel).messages) != before {
		t.Fatal("the prompt must wait for activation, not go out with the list")
	}
	sent, _ := updated.(ChatModel).handleConnectorsActivated(connectorsActivatedMsg{connector: testConnectors[0]})
	msgs := sent.(ChatModel).messages
	last := msgs[len(msgs)-1]
	if last.Role != RoleUser || !strings.Contains(last.Content, "I just connected Google") {
		t.Fatalf("want the personalization prompt sent as a user turn, got %+v", last)
	}
}

func TestPersonalizeOfAToolServerSaysAccessStaysAndHowToRevoke(t *testing.T) {
	m := slackModel(t)
	slack := gaiaconnectors.Connector{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server", Configured: true}
	sent, _ := m.handleConnectorsActivated(connectorsActivatedMsg{connector: slack})
	if text := lastStatus(t, sent.(ChatModel)); !strings.Contains(text, "keeps that access") ||
		!strings.Contains(text, "/connectors revoke mcp-slack") {
		t.Fatalf("the user must be told GAIA keeps access and how to turn it off: %s", text)
	}
	msgs := sent.(ChatModel).messages
	if last := msgs[len(msgs)-1]; last.Role != RoleUser {
		t.Fatal("the pass must still go out after the notice")
	}
	// An OAuth pass activates nothing, so there is nothing new to disclose.
	plain, _ := slackModel(t).handleConnectorsActivated(connectorsActivatedMsg{connector: testConnectors[0]})
	for _, msg := range plain.(ChatModel).messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "revoke") {
			t.Fatalf("no revoke notice for an OAuth connector: %s", msg.Content)
		}
	}
}

func TestPersonalizeWaitsForARunningTurn(t *testing.T) {
	m := slackModel(t)
	m.streaming = true
	before := len(m.messages)
	updated, cmd := m.handleConnectorsActivated(connectorsActivatedMsg{connector: testConnectors[0]})
	if cmd == nil || len(updated.(ChatModel).messages) != before {
		t.Fatal("a pass must not start a second turn over a running one")
	}
}

func TestPersonalizeReportsAFailedActivation(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleConnectorsActivated(connectorsActivatedMsg{
		connector: testConnectors[1], err: errors.New("keyring locked"),
	})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "keyring locked") {
		t.Errorf("activation failure must be shown: %s", text)
	}
}

func TestPersonalizeRefusesASwitchedOffConnector(t *testing.T) {
	off := false
	rows := []gaiaconnectors.Connector{{ID: "mcp-notion", DisplayName: "Notion", Configured: true, Enabled: &off}}
	m := slackModel(t)
	updated, cmd := m.handleConnectorsList(connectorsListMsg{rows: rows, personalize: "mcp-notion"})
	if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "switched off") {
		t.Fatal("a switched-off connector must be refused before activation")
	}
}

func TestPersonalizeRefusesUnconnectedAndUnknown(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleConnectorsList(connectorsListMsg{rows: testConnectors, personalize: "mcp-slack"})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "isn't connected yet") {
		t.Errorf("unconnected: %s", text)
	}
	updated, _ = m.handleConnectorsList(connectorsListMsg{rows: testConnectors, personalize: "nope"})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "No connector named") {
		t.Errorf("unknown: %s", text)
	}
	updated, cmd := m.submit("/connectors personalize")
	if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "Name a connected connector") {
		t.Error("bare personalize must ask for an id without probing")
	}
}

func TestStatusBarConnectorsEntryIsClickable(t *testing.T) {
	m := slackModel(t)
	m.width, m.height = 120, 30
	plain := ansi.Strip(m.renderStatusBar())
	col := strings.Index(plain, connectorsHint)
	if col < 0 {
		t.Fatalf("idle status bar should show %q: %s", connectorsHint, plain)
	}
	x := ansi.StringWidth(plain[:col]) + 1
	press := tea.MouseMsg{X: x, Y: m.height - 1, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress}
	release := press
	release.Action = tea.MouseActionRelease
	pressed, _ := m.handleTranscriptMouse(press)
	clicked, cmd := pressed.(ChatModel).handleTranscriptMouse(release)
	if cmd == nil || !strings.Contains(lastStatus(t, clicked.(ChatModel)), "Checking connectors") {
		t.Fatal("a real press and release on the entry must open connectors")
	}
	if m.clickedConnectorsHint(0, m.height-1) || m.clickedConnectorsHint(ansi.StringWidth(plain[:col])+1, 2) {
		t.Fatal("clicks elsewhere must not open connectors")
	}
	m.streaming = true
	if strings.Contains(ansi.Strip(m.renderStatusBar()), connectorsHint) {
		t.Fatal("the entry must give way while a turn runs")
	}
}

var revokeRows = []gaiaconnectors.Connector{
	{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server", Configured: true,
		Activations: map[string]bool{gaiaconnectors.FlagshipAgentID: true}},
	{ID: "mcp-notion", DisplayName: "Notion", Type: "mcp_server", Configured: true,
		Activations: map[string]bool{"builtin:chat": true}},
	{ID: "google", DisplayName: "Google", Type: "oauth_pkce", Configured: true},
}

func stubDeactivate(t *testing.T) *[]string {
	t.Helper()
	var calls []string
	prev := deactivateConnectorCmd
	deactivateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
		calls = append(calls, c.ID)
		return func() tea.Msg { return connectorsRevokedMsg{connector: c} }
	}
	t.Cleanup(func() { deactivateConnectorCmd = prev })
	return &calls
}

func TestRevokeTurnsOffTheFlagshipsAccess(t *testing.T) {
	calls := stubDeactivate(t)
	m := slackModel(t)
	before := len(m.messages)
	updated, cmd := m.submit("/connectors revoke Slack")
	if cmd == nil {
		t.Fatal("/connectors revoke must look the connector up")
	}
	for _, msg := range updated.(ChatModel).messages[before:] {
		if msg.Role == RoleUser {
			t.Fatal("/connectors revoke must not go to the agent as a question")
		}
	}
	updated, cmd = updated.(ChatModel).handleConnectorsList(connectorsListMsg{rows: revokeRows, revoke: "Slack"})
	if cmd == nil || len(*calls) != 1 || (*calls)[0] != "mcp-slack" {
		t.Fatalf("want one deactivation of mcp-slack, got %v", *calls)
	}
	done, _ := updated.(ChatModel).handleConnectorsRevoked(cmd().(connectorsRevokedMsg))
	if text := lastStatus(t, done.(ChatModel)); !strings.Contains(text, "can no longer use Slack") {
		t.Fatalf("revoke must confirm: %s", text)
	}
}

func TestRevokeRefusesWhatItCannotOrNeedNotTurnOff(t *testing.T) {
	calls := stubDeactivate(t)
	m := slackModel(t)
	cases := map[string]string{
		"mcp-notion": "already can't use Notion", // another agent's activation is not GAIA's
		"google":     "Disconnect it",            // OAuth: no activation to revoke
		"nope":       "No connector named",
	}
	for name, want := range cases {
		updated, cmd := m.handleConnectorsList(connectorsListMsg{rows: revokeRows, revoke: name})
		if cmd != nil {
			t.Errorf("%s: must not run a deactivation", name)
		}
		if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, want) {
			t.Errorf("%s: want %q in %q", name, want, text)
		}
	}
	updated, cmd := m.submit("/connectors revoke")
	if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "/connectors revoke <id>") {
		t.Error("bare revoke must ask for an id without probing")
	}
	// "/connectors revokeX" is not the command.
	if isConnectorsRevoke("/connectors revokeall") {
		t.Error("a longer word must not match the revoke prefix")
	}
	if len(*calls) != 0 {
		t.Fatalf("no deactivation should have run, got %v", *calls)
	}
}

func TestRevokeStillRunsWhenAnOlderCLIReportsNoActivations(t *testing.T) {
	calls := stubDeactivate(t)
	rows := []gaiaconnectors.Connector{{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server", Configured: true}}
	_, cmd := slackModel(t).handleConnectorsList(connectorsListMsg{rows: rows, revoke: "mcp-slack"})
	if cmd == nil || len(*calls) != 1 {
		t.Fatal("without activation data the revoke must still run, not claim GAIA has no access")
	}
}

func TestRevokeReportsAFailedDeactivation(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleConnectorsRevoked(connectorsRevokedMsg{
		connector: revokeRows[0], err: errors.New("ledger locked"),
	})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "ledger locked") {
		t.Errorf("deactivation failure must be shown: %s", text)
	}
}
