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
// model, and Lemonade must serve the id. The next turn then carries it, and only
// a turn the agent completes on it confirms it; a refusal puts the old model back.

// relayedSwitchTimeout bounds the check: a daemon attach, a contract read, and
// one Lemonade model listing.
const relayedSwitchTimeout = 30 * time.Second

// relayedSwitchMsg is the answer to a relayed switch check. target is
// Lemonade's own spelling of the id once the check has passed.
type relayedSwitchMsg struct {
	provider string
	target   string
	err      error
}

// relayedHeader is what the header showed, so a refused switch can restore it.
type relayedHeader struct {
	modelID, modelDisplay, modelBackend string
	modelRemote                         bool
}

// relayedPending is a switch the next turn carries but the agent has not
// confirmed yet.
type relayedPending struct {
	choice   savedChoice
	prevTurn string
	prev     relayedHeader
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
		id, err := checkRelayedSwitch(ctx, sw, base, provider, target)
		if err != nil {
			return relayedSwitchMsg{provider: provider, target: target, err: err}
		}
		return relayedSwitchMsg{provider: provider, target: id}
	}
}

// checkRelayedSwitch returns Lemonade's id for target, or why it cannot be
// switched to.
func checkRelayedSwitch(ctx context.Context, sw client.TurnModelSwitcher, base, provider, target string) (string, error) {
	if client.IsClaudeModelID(target) {
		return "", errors.New("Claude is not available when GAIA runs through the background service")
	}
	if err := sw.CheckModelSwitch(ctx); err != nil {
		return "", err
	}
	models, err := lemonade.New(base).Models(ctx, provider)
	if err != nil {
		if errors.Is(err, lemonade.ErrUnreachable) {
			return "", errors.New("the Lemonade server is not answering")
		}
		return "", err
	}
	for _, mdl := range models {
		if strings.EqualFold(mdl.ID, target) {
			return mdl.ID, nil
		}
	}
	if provider == "local" {
		return "", errors.New("it is not downloaded — download it with /provider")
	}
	return "", errors.New(lemonade.Label(provider) +
		" does not list it — the key may be missing or the provider is not connected")
}

// handleRelayedSwitch applies a checked switch, or says why it was not made.
// The opening restore or gate pick reports through the startup path, so a
// failure there opens the picker the same way it does on the direct route.
func (m ChatModel) handleRelayedSwitch(msg relayedSwitchMsg) (tea.Model, tea.Cmd) {
	m.relayedSwitching = false
	startup := m.startup.inFlight && strings.EqualFold(msg.target, m.startup.model)
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
	m.relayedPending = &relayedPending{
		choice:   savedChoice{provider: msg.provider, model: msg.target},
		prevTurn: sw.TurnModel(),
		prev:     relayedHeader{m.modelID, m.modelDisplay, m.modelBackend, m.modelRemote},
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
	if !startup {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "Switched to " + choiceLabel(msg.provider, msg.target) + ". Your next message runs on it.",
		})
	}
	m.updateViewport()
	return m, nil
}

// confirmRelayedSwitch remembers a pending switch once a turn has completed on it.
func (m *ChatModel) confirmRelayedSwitch() {
	if m.relayedPending == nil {
		return
	}
	m.rememberModel(m.relayedPending.choice)
	m.relayedPending = nil
}

// revertRelayedSwitch puts the model back when the agent refused to move onto
// it. Reports whether err was that refusal.
func (m *ChatModel) revertRelayedSwitch(err error) bool {
	var refused *client.ModelSwitchRefusedError
	if m.relayedPending == nil || !errors.As(err, &refused) {
		return false
	}
	if sw, ok := m.relayedSwitcher(); ok {
		sw.SetTurnModel(m.relayedPending.prevTurn)
	}
	prev := m.relayedPending.prev
	m.modelID, m.modelDisplay, m.modelBackend, m.modelRemote =
		prev.modelID, prev.modelDisplay, prev.modelBackend, prev.modelRemote
	m.relayedPending = nil
	return true
}
