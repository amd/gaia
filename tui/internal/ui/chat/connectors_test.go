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
