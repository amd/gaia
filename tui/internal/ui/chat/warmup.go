// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The warm-up stage: between the readiness gate and the chat, the flagship
// agent starts, loads its model and reads its system prompt — work the first
// question used to pay for while the user watched a spinner with no reason
// given. It runs as an ordinary turn on a sentinel query, so the agent's
// startup status (the model chip) arrives through the normal path; the stage
// only changes how that turn is shown.
//
// Esc hides the stage without cancelling: cancelling a turn on this transport
// kills the agent, which would throw away exactly the work being done. Anything
// typed meanwhile queues and is sent the moment the agent is ready.

// warmUpStartedStep names the work before the agent can report anything: the
// process starting and the agent being built.
const warmUpStartedStep = "Starting the agent"

type startWarmUpMsg struct{}

// warmUpApplies reports whether this session should warm up before chatting.
// Only the flagship over its own stdio transport understands the sentinel.
func (m ChatModel) warmUpApplies() bool {
	w, ok := m.client.(client.WarmUpper)
	return ok && w.SupportsWarmUp() && m.agentID == setupAgentID && m.initialQuery == ""
}

func (m ChatModel) startWarmUp() (tea.Model, tea.Cmd) {
	m.warming = true
	m.warmStart = time.Now()
	m.warmStep = warmUpStartedStep
	m.warmDone = nil
	next, cmd := m.startTurn(client.WarmUpQuery)
	cm := next.(ChatModel)
	// The live line for when the stage is hidden.
	cm.setLiveStatus("Getting GAIA ready: " + warmUpStartedStep)
	return cm, cmd
}

// hideWarmUpStage is Esc on the stage: show the chat, keep warming up.
func (m ChatModel) hideWarmUpStage() (tea.Model, tea.Cmd) {
	m.warmHidden = true
	m.updateViewport()
	return m, nil
}

// advanceWarmStep records the agent's next step; the previous one is done.
func (m *ChatModel) advanceWarmStep(step string) {
	if m.warmStep != "" && m.warmStep != step {
		m.warmDone = append(m.warmDone, m.warmStep)
	}
	m.warmStep = step
}

// firstPromptHint gives a blank first screen somewhere to start.
const firstPromptHint = "Try: \"What's in my Documents folder?\" · \"Summarize the report in my Downloads\" · " +
	"\"Total sales by region in sales.csv\" · /help for commands"

// hasUserMessage reports whether the user has said anything this session.
func (m ChatModel) hasUserMessage() bool {
	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			return true
		}
	}
	return false
}

// finishWarmUp ends the stage. A skipped warm-up (a remote model) leaves no
// trace; a finished one says how long it took, once.
func (m *ChatModel) finishWarmUp(answer string) {
	elapsed := time.Since(m.warmStart).Round(100 * time.Millisecond)
	m.warming = false
	m.warmHidden = false
	m.streaming = false
	m.activity = nil
	m.buffer = ""
	if answer == client.WarmedUp {
		model := m.modelDisplay
		if model == "" {
			model = "the model"
		}
		ready := "[✓] Ready — " + model + " is loaded and has read its instructions (" + elapsed.String() + ")."
		if len(m.queued) == 0 && !m.hasUserMessage() {
			ready += "\n" + firstPromptHint
		}
		m.messages = append(m.messages, Message{Role: RoleStatus, Content: ready})
	}
	m.settleTurn()
	m.updateViewport()
}

var (
	warmTitleStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Text)
	warmDoneStyle  = lipgloss.NewStyle().Foreground(theme.Success)
	warmStepStyle  = lipgloss.NewStyle().Foreground(theme.Text)
	warmNoteStyle  = lipgloss.NewStyle().Foreground(theme.Dim)
)

// renderWarmUpStage is the stage shown in place of the transcript, height rows tall.
func (m ChatModel) renderWarmUpStage(height int) string {
	var lines []string
	lines = append(lines, warmTitleStyle.Render("Getting GAIA ready"), "")
	for _, step := range m.warmDone {
		lines = append(lines, warmDoneStyle.Render("✓ ")+warmStepStyle.Render(step))
	}
	clock := formatElapsed(time.Since(m.warmStart))
	lines = append(lines, m.spinner.View()+" "+warmStepStyle.Render(m.warmStep)+"  "+warmNoteStyle.Render(clock))
	lines = append(lines, "",
		warmNoteStyle.Render("This happens once per session, so your first answer starts right away."),
		warmNoteStyle.Render("Type now — it's sent the moment GAIA is ready · Esc to show the chat"))

	width := m.width - 4
	if width < 20 {
		width = 20
	}
	body := strings.Join(lines, "\n")
	box := lipgloss.NewStyle().Width(width).Padding(1, 2).Render(body)
	return lipgloss.Place(m.width, height, lipgloss.Left, lipgloss.Center, box)
}
