// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package chat

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/providers"
	tea "github.com/charmbracelet/bubbletea"
)

func TestProviderPanelNeverSendsCredentialAsChat(t *testing.T) {
	c := &queryCapturingClient{}
	m := NewChatModelForFlagship(c, "gaia", "GAIA", true, true)
	m.width = 80
	m.height = 24
	updated, _ := m.submit("/provider")
	m = updated.(ChatModel)
	for _, key := range []tea.KeyMsg{{Type: tea.KeyDown}, {Type: tea.KeyEnter}, {Type: tea.KeyRunes, Runes: []rune("sensitive-test-value"), Paste: true}} {
		updated, _ = m.Update(key)
		m = updated.(ChatModel)
	}
	if len(c.sent) != 0 || strings.Contains(m.View(), "sensitive-test-value") {
		t.Fatal("credential escaped provider input")
	}
	if m.ControlSnapshot().Overlay != "provider" {
		t.Fatal("control API cannot identify provider panel")
	}
	updated, _ = m.Update(providers.ClosedMsg{})
	m = updated.(ChatModel)
	if m.providerPanel != nil {
		t.Fatal("panel did not close")
	}
}
func TestCloudHeaderDoesNotCallFireworksClaude(t *testing.T) {
	m, _ := newTestModel(t)
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", ModelID: "fireworks.gemma-4-31b-it", ModelDisplay: "Gemma 4 31B IT", ModelBackend: "fireworks", ModelRemote: true})
	if m.claudeMode || strings.Contains(m.renderHeader(), "claude") || !strings.Contains(m.renderHeader(), "Fireworks AI") {
		t.Fatal("cloud provider mislabeled")
	}
	if !m.skipLocalChatSetup() {
		t.Fatal("cloud setup would download local chat")
	}
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", ModelID: "Gemma-4-E4B-it-GGUF", ModelDisplay: "Gemma", ModelBackend: "lemonade"})
	if m.skipLocalChatSetup() {
		t.Fatal("local setup must restore local chat requirement")
	}
}

type cloudLaunchClient struct{ nullClient }

func (*cloudLaunchClient) ModelAtLaunch() string { return "fireworks.gemma-4-31b-it" }
func TestCloudLaunchHasProviderBeforeFirstAgentEvent(t *testing.T) {
	m := NewChatModelForFlagship(&cloudLaunchClient{}, "gaia", "GAIA", true, true)
	if !m.skipLocalChatSetup() || !m.modelRemote || m.modelBackend != "fireworks" {
		t.Fatal("cloud launch lost before first turn")
	}
	if !strings.Contains(m.renderHeader(), "Fireworks AI") {
		t.Fatal("initial header missing provider")
	}
}

func TestProviderSelectionSetsUnstartedChildModel(t *testing.T) {
	c := client.NewCanonicalSubprocessClient("unused", []string{"--use-claude", "--dev"}, true)
	m := NewChatModelForFlagship(c, "gaia", "GAIA", true, true)
	m.width, m.height = 80, 24
	updated, _ := m.submit("/provider")
	m = updated.(ChatModel)
	updated, _ = m.Update(providers.SelectedMsg{ID: "fireworks.gemma-4-31b-it"})
	m = updated.(ChatModel)
	if c.ModelAtLaunch() != "fireworks.gemma-4-31b-it" || c.ClaudeAtLaunch() || m.claudeMode || !m.modelRemote || !m.skipLocalChatSetup() {
		t.Fatal("first provider selection would start the wrong backend")
	}
}
