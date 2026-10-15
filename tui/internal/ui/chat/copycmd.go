// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/ui/cards"
)

// Keyboard copy: /copy picks any message with the arrow keys, /copy all takes
// the whole conversation as markdown. Neither needs the mouse, so neither
// depends on who owns it or on the message fitting on screen.

const (
	copyCommand    = "/copy"
	copyAllCommand = "/copy all"
)

// isCopyCommand reports whether query is one of the copy commands. They are
// local and read-only, so they run mid-turn instead of queueing behind it.
func isCopyCommand(query string) bool {
	return query == copyCommand || query == copyAllCommand
}

// messagePicker is /copy's state: which message the arrow keys are on.
type messagePicker struct {
	open  bool
	index int
}

// startMessagePick opens the picker on the latest answer (or the latest
// message, when there is no answer yet).
func (m ChatModel) startMessagePick() (tea.Model, tea.Cmd) {
	if len(m.messages) == 0 {
		return m.statusNote("Nothing to copy yet."), nil
	}
	idx := len(m.messages) - 1
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleAssistant {
			idx = i
			break
		}
	}
	m.sel = textSelection{}
	m.pick = messagePicker{open: true, index: idx}
	m.scrollToMessage(idx)
	return m, nil
}

// handlePickKey drives the open picker. handled is false for a key the picker
// does not own; the picker closes and the key does what it always does.
func (m ChatModel) handlePickKey(msg tea.KeyMsg) (ChatModel, tea.Cmd, bool) {
	if m.pick.index < 0 || m.pick.index >= len(m.messages) {
		m.pick = messagePicker{}
		return m, nil, false
	}
	switch msg.Type {
	case tea.KeyUp:
		if m.pick.index > 0 {
			m.pick.index--
		}
	case tea.KeyDown:
		if m.pick.index < len(m.messages)-1 {
			m.pick.index++
		}
	case tea.KeyHome:
		m.pick.index = 0
	case tea.KeyEnd:
		m.pick.index = len(m.messages) - 1
	case tea.KeyEnter:
		text := m.pickedText()
		m.pick = messagePicker{}
		return m, copyToClipboard(text, "message"), true
	case tea.KeyEsc:
		m.pick = messagePicker{}
		return m, nil, true
	default:
		m.pick = messagePicker{}
		return m, nil, false
	}
	m.scrollToMessage(m.pick.index)
	return m, nil, true
}

// pickedText is the picked message's source, or its rendered text with the
// chrome stripped when it has none (a card).
func (m ChatModel) pickedText() string {
	msg := &m.messages[m.pick.index]
	if src, ok := messageCopyText(msg); ok {
		return src
	}
	for _, sp := range m.msgSpans {
		if sp.index == m.pick.index {
			return extractSelection(strings.Split(m.vpContent, "\n"), m.msgSpans, m.messages,
				selPos{row: sp.start}, selPos{row: sp.end - 1, col: m.viewport.Width})
		}
	}
	return ""
}

// scrollToMessage brings message idx into view, top first when it is taller
// than the window.
func (m *ChatModel) scrollToMessage(idx int) {
	for _, sp := range m.msgSpans {
		if sp.index != idx {
			continue
		}
		top, h := m.viewport.YOffset, m.viewport.Height
		switch {
		case sp.start < top || sp.end-sp.start > h:
			m.viewport.SetYOffset(sp.start)
		case sp.end > top+h:
			m.viewport.SetYOffset(sp.end - h)
		}
		*m = m.afterScroll()
		return
	}
}

// transcriptMarkdown is the whole conversation as one markdown document:
// questions and answers under a heading each, a turn's steps as a list, an
// error as a quote and a card as preformatted text. Status notes are left out —
// they are the TUI talking about itself ("sent to the clipboard"), not part of
// the conversation.
func transcriptMarkdown(msgs []Message, agentName string) string {
	if agentName == "" {
		agentName = "Agent"
	}
	var parts []string
	for i := range msgs {
		msg := &msgs[i]
		switch msg.Role {
		case RoleUser:
			parts = append(parts, "## You\n\n"+msg.Content)
		case RoleAssistant:
			parts = append(parts, "## "+agentName+"\n\n"+msg.Content)
		case RoleError:
			parts = append(parts, "> **Error:** "+strings.ReplaceAll(msg.Content, "\n", "\n> "))
		case RoleWork:
			var rows []string
			for _, item := range msg.Work {
				c := strings.TrimSpace(item.Content)
				if c == "" {
					continue
				}
				if d := strings.TrimSpace(item.Detail); d != "" {
					c += " — " + d
				}
				rows = append(rows, "- "+c)
			}
			if len(rows) > 0 {
				parts = append(parts, strings.Join(rows, "\n"))
			}
		case RoleCard:
			drawn := strings.TrimRight(ansi.Strip(cards.Render(msg.Render, msg.Data, 80)), "\n ")
			if strings.TrimSpace(drawn) != "" {
				parts = append(parts, "```text\n"+drawn+"\n```")
			}
		}
	}
	if len(parts) == 0 {
		return ""
	}
	return strings.Join(parts, "\n\n") + "\n"
}
