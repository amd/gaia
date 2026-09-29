// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/providers"
)

// The model a session runs on, remembered across launches.
//
// The host (root.FlagshipModel) can ask the chat to restore the choice saved by
// an earlier launch, or to confirm one just picked at the readiness gate. Either
// is one `/model` turn sent as the chat opens, so the agent — the only thing
// that can see what Lemonade actually serves — validates it.
//
// Only a switch the agent confirms counts. Every other ending — a refusal, an
// agent that died first, a turn the user stopped — is said in one line and the
// provider picker opens; the session is never quietly left on another model.

// StartupModelKind is what the opening `/model` turn is for.
type StartupModelKind int

const (
	startupNone StartupModelKind = iota
	// StartupRestore re-applies the choice saved by an earlier launch.
	StartupRestore
	// StartupPicked confirms a model picked at the readiness gate.
	StartupPicked
)

type startupModel struct {
	kind     StartupModelKind
	provider string
	model    string
	pending  bool // not sent yet
	inFlight bool // its turn has not ended
	pinged   bool // the agent reported its model during that turn
	// confirmed is set when the agent switched to exactly this model.
	confirmed bool
	// unresolved is set when the turn ended any other way; Update reports it.
	unresolved bool
	// reason is the clause the failure line gives, when one is known.
	reason string
}

// savedChoice is a provider + model pair as the agent reported it.
type savedChoice struct {
	provider string
	model    string
}

type startupModelMsg struct{}

// WithStartupModel arms the opening `/model` turn for provider + model.
func (m ChatModel) WithStartupModel(kind StartupModelKind, provider, model string) ChatModel {
	model = strings.TrimSpace(model)
	if model == "" || (kind != StartupRestore && kind != StartupPicked) {
		m.startup = startupModel{}
		return m
	}
	m.startup = startupModel{kind: kind, provider: provider, model: model, pending: true}
	return m
}

// WithModelMemory sets where a confirmed model switch is saved.
func (m ChatModel) WithModelMemory(save func(provider, model string) error) ChatModel {
	m.saveModel = save
	return m
}

// WithExpectedModel names, in the header, the local model the readiness gate
// loaded for this session, until the agent's own model-state ping replaces it.
// Ignored when the launch already names a model or runs on Claude.
func (m ChatModel) WithExpectedModel(id string) ChatModel {
	if id = strings.TrimSpace(id); id == "" || m.modelDisplay != "" || m.claudeMode {
		return m
	}
	// Display only: modelID stays empty so the first ping is not mistaken for
	// an unrequested revert.
	m.modelDisplay = id
	m.modelBackend = "lemonade"
	return m
}

func startupModelCmd() tea.Cmd { return func() tea.Msg { return startupModelMsg{} } }

// runStartupModel sends the opening `/model` turn.
func (m ChatModel) runStartupModel() (tea.Model, tea.Cmd) {
	if !m.startup.pending {
		return m, nil
	}
	m.startup.pending = false
	if !m.supportsModelCommand() {
		return m, nil
	}
	m.startup.inFlight = true
	updated, cmd := m.submit(modelCommandPrefix + " " + m.startup.model)
	next := updated.(ChatModel)
	if next.streaming {
		return next, cmd
	}
	// Refused before any turn started (an id this build does not accept):
	// that refusal is the reason, shown the same way an agent's would be.
	next.startup.inFlight = false
	detail := ""
	if n := len(next.messages); n > 0 && next.messages[n-1].Role == RoleError {
		detail = next.messages[n-1].Content
		next.messages = next.messages[:n-1]
	}
	next.startup.reason = startupFailureReason(next.startup.provider, detail)
	return next.startupRefused()
}

// endStartupTurn records that the opening turn is over. Called from every
// path that ends a turn, so none can leave it looking in flight.
func (m *ChatModel) endStartupTurn(reason string) {
	if !m.startup.inFlight {
		return
	}
	m.startup.inFlight = false
	if m.startup.confirmed {
		return
	}
	m.startup.unresolved = true
	if m.startup.reason == "" {
		m.startup.reason = reason
	}
}

// startupRefused reports a restore or pick that did not take, and hands the
// choice back to the user.
func (m ChatModel) startupRefused() (ChatModel, tea.Cmd) {
	m.startup.unresolved = false
	m.messages = append(m.messages, Message{Role: RoleError, Content: startupFailureLine(m.startup)})
	panel := providers.New(m.lemonadeBaseURL, m.width, m.height)
	m.providerPanel = &panel
	m.palette.open = false
	m.updateViewport()
	return m, panel.Init()
}

// rememberModel saves a switch the agent confirmed. A failed save is said, not
// swallowed: next launch would otherwise come back on a different model.
func (m *ChatModel) rememberModel(choice savedChoice) {
	if m.saveModel == nil || choice.model == "" {
		return
	}
	if err := m.saveModel(choice.provider, choice.model); err != nil {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: "This model will not be remembered for the next launch: " + err.Error(),
		})
	}
}

// providerOfBackend maps a ping's model_backend onto the provider names the
// picker and config use.
func providerOfBackend(backend string) string {
	if backend == "" || backend == "lemonade" {
		return "local"
	}
	return backend
}

// choiceLabel names a saved choice the way the header does.
func choiceLabel(provider, model string) string {
	switch provider {
	case "claude":
		return claudeLaunchName(model)
	case "", "local":
		return model
	}
	return lemonade.Label(provider) + " · " + strings.TrimPrefix(model, provider+".")
}

// startupFailureLine is the one line shown when the opening switch did not take.
func startupFailureLine(s startupModel) string {
	lead := "Couldn't restore your last model, "
	if s.kind == StartupPicked {
		lead = "Couldn't switch to "
	}
	reason := s.reason
	if reason == "" {
		reason = "the agent did not confirm the switch"
	}
	return lead + choiceLabel(s.provider, s.model) + ": " + reason +
		". Pick a model to continue, now or later with /provider."
}

// startupFailureReason turns the agent's refusal into a clause a user can act on.
func startupFailureReason(provider, detail string) string {
	switch {
	case strings.Contains(detail, "not reachable"):
		return "the Lemonade server is not answering"
	case strings.Contains(detail, "Unknown Lemonade model"):
		if provider == "" || provider == "local" {
			return "it is not downloaded"
		}
		return lemonade.Label(provider) + " does not list it — the key may be missing or the provider is not connected"
	case strings.Contains(detail, "ANTHROPIC_API_KEY"):
		return "ANTHROPIC_API_KEY is not set"
	}
	reason := strings.TrimSpace(detail)
	if i := strings.Index(reason, ". "); i > 0 {
		reason = reason[:i]
	}
	reason = strings.TrimSuffix(reason, ".")
	if reason == "" {
		return "the agent refused it"
	}
	return truncateRunes(reason, 160)
}
