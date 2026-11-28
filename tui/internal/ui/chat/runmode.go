// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// `/mode` is the Agent UI's Local/Cloud switch. A mode is its chat model, so
// switching runs that model through `/model`: the same checks, the same
// confirmation, and the same save, which records the mode (preflight.WriteLastModel).

const modeCommandPrefix = "/mode"

func isModeCommand(query string) bool {
	trimmed := strings.TrimSpace(query)
	return trimmed == modeCommandPrefix || strings.HasPrefix(trimmed, modeCommandPrefix+" ")
}

func modeCommandArg(query string) string {
	return strings.ToLower(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(query), modeCommandPrefix)))
}

// WithRunModes sets where the run modes are read from; tests pass a fake.
func (m ChatModel) WithRunModes(read func() (preflight.RunModeState, error)) ChatModel {
	m.readRunModes = read
	return m
}

// currentMode is the mode the chat model in use puts GAIA in, or "" while
// that isn't known yet: before the agent names a model, or while the model
// from the last launch is still being restored.
func (m ChatModel) currentMode() string {
	if m.currentModel() == "" || m.startup.pending || m.startup.inFlight {
		return ""
	}
	if m.modelRemote {
		return preflight.CloudMode
	}
	return preflight.LocalMode
}

// currentModel is the model chat runs on as far as the header knows it.
func (m ChatModel) currentModel() string {
	if m.modelID != "" {
		return m.modelID
	}
	return m.modelDisplay
}

func modeName(mode string) string {
	if mode == preflight.CloudMode {
		return "Cloud"
	}
	return "Local"
}

func (m ChatModel) runModeCommand(query string) (tea.Model, tea.Cmd) {
	if !m.supportsModelCommand() {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: m.agentName + " does not support run modes (/mode) — only the gaia flagship agent does.",
		})
		m.updateViewport()
		return m, nil
	}
	read := m.readRunModes
	if read == nil {
		read = preflight.ReadRunModeState
	}
	state, err := read()
	if err != nil {
		m.messages = append(m.messages, Message{Role: RoleError, Content: "The run modes can't be read: " + err.Error()})
		m.updateViewport()
		return m, nil
	}
	current := m.currentMode()
	arg := modeCommandArg(query)
	if arg == "" {
		return m.statusNote(m.describeModes(state, current)), nil
	}
	if arg != preflight.LocalMode && arg != preflight.CloudMode {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: "'" + arg + "' is not a run mode. Use /mode local or /mode cloud.",
		})
		m.updateViewport()
		return m, nil
	}
	if arg == current {
		return m.statusNote("Already in " + modeName(arg) + " on " + m.modelLabelFor(m.currentModel()) + "."), nil
	}
	if arg == preflight.CloudMode && lemonade.LocalOnly() {
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: "Local only is on, so Cloud can't be used. " + lemonade.LocalOnlyHint,
		})
		m.updateViewport()
		return m, nil
	}
	return m.submit(modelCommandPrefix + " " + state.ChatFor(arg))
}

// describeModes is `/mode` alone: both modes, which is in use, and what each runs.
func (m ChatModel) describeModes(state preflight.RunModeState, current string) string {
	lines := []string{"Run modes:"}
	for _, mode := range preflight.RunModes {
		model := state.ChatFor(mode)
		if mode == current {
			model = m.currentModel()
		}
		line := "  " + modeName(mode) + " · " + m.modelLabelFor(model)
		if mode == current {
			line += "  (in use)"
		}
		lines = append(lines, line)
	}
	lines = append(lines, "Cloud moves chat only; reading images, speech, image creation and documents stay on this PC.")
	if lemonade.LocalOnly() {
		lines = append(lines, "Local only is on, so Cloud can't be used. "+lemonade.LocalOnlyHint)
	} else {
		lines = append(lines, "Switch with /mode local or /mode cloud. Pick another model for a mode with /provider.")
	}
	return strings.Join(lines, "\n")
}

// modelLabelFor names a model the way the header does: a cloud model with its provider.
func (m ChatModel) modelLabelFor(model string) string {
	if provider, name, ok := strings.Cut(model, "."); ok && lemonade.IsCloudID(model) {
		return lemonade.Label(provider) + " · " + name[strings.LastIndex(name, "/")+1:]
	}
	return model
}
