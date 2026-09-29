// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package root

import (
	"strings"
	"sync"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// lastModel is the flagship's remembered choice, shared by every copy Bubble
// Tea makes of the model so a switch confirmed in chat is what an /agents
// switch back to the flagship restores.
type lastModel struct {
	mu       sync.Mutex
	provider string
	model    string
}

func (l *lastModel) get() (provider, model string) {
	if l == nil {
		return "", ""
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.provider, l.model
}

func (l *lastModel) set(provider, model string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.provider, l.model = provider, model
}

// WithSavedModel restores the model an earlier launch chose. It is kept apart
// from --model: only the flagship can switch models, so another agent must
// never be launched on it.
func (m FlagshipModel) WithSavedModel(saved preflight.LastModel) FlagshipModel {
	if m.last == nil {
		m.last = &lastModel{}
	}
	if saved.Set() {
		m.last.set(providerOf(saved.Provider, saved.Model), saved.Model)
	}
	return m
}

// WithModelMemory sets where a model switch the agent confirmed is saved.
func (m FlagshipModel) WithModelMemory(save func(provider, model string) error) FlagshipModel {
	if m.last == nil {
		m.last = &lastModel{}
	}
	last := m.last
	m.saveModel = func(provider, model string) error {
		last.set(provider, model)
		if save == nil {
			return nil
		}
		return save(provider, model)
	}
	return m
}

// WithLaunchNotice carries a status line into the first chat frame.
func (m FlagshipModel) WithLaunchNotice(text string) FlagshipModel {
	m.launchNotice = text
	return m
}

// restoring reports the saved choice to restore for agent, if any. Explicit
// launch flags (--model, --use-claude) and a gate pick win over it.
func (m FlagshipModel) restoring(agent catalog.Agent) (provider, model string, ok bool) {
	if agent.ID != catalog.FlagshipID || m.model != "" || m.useClaude {
		return "", "", false
	}
	provider, model = m.last.get()
	return provider, model, model != ""
}

// providerOf trusts the model id over a stored provider it contradicts, so a
// hand-edited config cannot send a Claude id to Lemonade or the reverse.
func providerOf(provider, model string) string {
	switch {
	case client.IsClaudeModelID(model):
		return "claude"
	case lemonade.IsCloudID(model):
		return strings.SplitN(model, ".", 2)[0]
	case provider == "claude" || provider == "fireworks" || provider == "amd":
		return "local"
	}
	return provider
}

// launchModel is the model agent is started on. A saved cloud model is started
// directly: the gate then skips the local chat load, and the header names it at
// once. A saved local or Claude model is applied by the opening /model turn
// instead, so a deleted model or a missing key reaches the chat's one-line
// refusal rather than halting the launch at the gate.
func (m FlagshipModel) launchModel(agent catalog.Agent) string {
	if _, model, ok := m.restoring(agent); ok && lemonade.IsCloudID(model) {
		return model
	}
	return m.model
}

// pickedAtGate records a model chosen in the gate's provider panel, so the
// chat confirms it with the agent and remembers it.
func (m FlagshipModel) pickedAtGate(id string) FlagshipModel {
	m.startupKind = chat.StartupPicked
	m.startupProvider = "local"
	if lemonade.IsCloudID(id) {
		m.startupProvider = strings.SplitN(id, ".", 2)[0]
	}
	m.startupModel = id
	return m
}

// withStartupModel hands the chat its opening /model turn and memory. A gate
// pick is consumed by the launch that uses it.
func (m *FlagshipModel) withStartupModel(agent catalog.Agent, c chat.ChatModel, expected string) chat.ChatModel {
	c = c.WithModelMemory(m.saveModel)
	switch {
	case m.startupKind == chat.StartupPicked && agent.ID == catalog.FlagshipID:
		c = c.WithStartupModel(chat.StartupPicked, m.startupProvider, m.startupModel)
		// The pick did its job; from here the remembered choice (which the
		// pick becomes once confirmed) decides, and no other agent gets it.
		if m.model == m.startupModel {
			m.model = ""
		}
	default:
		if provider, model, ok := m.restoring(agent); ok {
			c = c.WithStartupModel(chat.StartupRestore, provider, model)
		}
	}
	m.startupKind = 0
	m.startupProvider, m.startupModel = "", ""
	return c.WithExpectedModel(expected)
}
