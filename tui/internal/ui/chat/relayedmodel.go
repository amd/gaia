// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/providers"
)

// `/model` over the daemon relay.
//
// The relay sends the model with every turn, so the agent has no `/model` turn
// to answer — sent as one, it is a question. Here the switch is checked before
// it is made: the agent must be new enough to move a conversation onto another
// model, and Lemonade must serve the id. Only then does the next turn carry it.

// relayedSwitchTimeout bounds the check: a daemon attach, a contract read, and
// one Lemonade model listing.
const relayedSwitchTimeout = 30 * time.Second

// relayedSwitchMsg is the answer to a relayed switch check.
type relayedSwitchMsg struct {
	provider string
	target   string
	err      error
}

// relayedSwitcher is the connection's turn-model setter, when it has one.
func (m ChatModel) relayedSwitcher() (client.TurnModelSwitcher, bool) {
	sw, ok := m.client.(client.TurnModelSwitcher)
	return sw, ok
}

// switchRelayedModel starts the check for `/model <target>`. A bare `/model`
// opens the picker, which lists what Lemonade serves.
func (m ChatModel) switchRelayedModel(sw client.TurnModelSwitcher, target string) (tea.Model, tea.Cmd) {
	if target == "" {
		panel := providers.New(m.lemonadeBaseURL, m.width, m.height)
		m.providerPanel = &panel
		m.palette.open = false
		return m, panel.Init()
	}
	provider := "local"
	if lemonade.IsCloudID(target) {
		provider = strings.SplitN(target, ".", 2)[0]
	}
	m.relayedSwitching = true
	base := m.lemonadeBaseURL
	return m, func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), relayedSwitchTimeout)
		defer cancel()
		return relayedSwitchMsg{provider: provider, target: target, err: checkRelayedSwitch(ctx, sw, base, provider, target)}
	}
}

// checkRelayedSwitch returns why target cannot be switched to, or nil.
func checkRelayedSwitch(ctx context.Context, sw client.TurnModelSwitcher, base, provider, target string) error {
	if client.IsClaudeModelID(target) {
		return errors.New("Claude is not available when GAIA runs through the background service")
	}
	if err := sw.CheckModelSwitch(ctx); err != nil {
		return err
	}
	models, err := lemonade.New(base).Models(ctx, provider)
	if err != nil {
		if errors.Is(err, lemonade.ErrUnreachable) {
			return errors.New("the Lemonade server is not answering")
		}
		return err
	}
	for _, mdl := range models {
		if strings.EqualFold(mdl.ID, target) {
			return nil
		}
	}
	if provider == "local" {
		return errors.New("it is not downloaded — download it with /provider")
	}
	return errors.New(lemonade.Label(provider) +
		" does not list it — the key may be missing or the provider is not connected")
}

// handleRelayedSwitch applies a checked switch, or says why it was not made.
// The opening restore or gate pick reports through the startup path, so a
// failure there opens the picker the same way it does on the direct route.
func (m ChatModel) handleRelayedSwitch(msg relayedSwitchMsg) (tea.Model, tea.Cmd) {
	m.relayedSwitching = false
	startup := m.startup.inFlight && msg.target == m.startup.model
	if startup {
		m.startup.inFlight = false
	}
	if msg.err != nil {
		reason := strings.TrimSuffix(strings.TrimSpace(msg.err.Error()), ".")
		if startup {
			m.startup.unresolved = true
			m.startup.reason = reason
			return m, nil
		}
		m.messages = append(m.messages, Message{
			Role: RoleError,
			Content: "Couldn't switch to " + choiceLabel(msg.provider, msg.target) + ": " + reason +
				". Nothing was switched — this session is still on " + m.currentModelName() + ".",
		})
		m.updateViewport()
		return m, nil
	}
	sw, ok := m.relayedSwitcher()
	if !ok {
		return m, nil
	}
	sw.SetTurnModel(msg.target)
	m.modelID = msg.target
	m.modelDisplay = msg.target
	m.modelRemote = msg.provider != "local"
	m.modelBackend = "lemonade"
	if m.modelRemote {
		m.modelBackend = msg.provider
	}
	m.claudeMode = false
	m.rememberModel(savedChoice{provider: msg.provider, model: msg.target})
	if !startup {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "Switched to " + choiceLabel(msg.provider, msg.target) + ". Your next message runs on it.",
		})
	}
	m.updateViewport()
	return m, nil
}
