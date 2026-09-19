// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/client"
)

// followUpTimeout bounds one delivery. The relay is on loopback, so this is a
// stuck-socket guard, not a budget — and it must stay well under the stream's
// own read timeout, since a follow-up that outlives the turn it was meant for
// has nowhere useful to land.
const followUpTimeout = 15 * time.Second

// followUpSentMsg confirms the sidecar took a mid-turn message. Carries the
// text because the model only writes it into the transcript once the agent
// provably has it — the point of the whole exchange is that "sent" means sent.
type followUpSentMsg struct{ text string }

// followUpFailedMsg reports a delivery that did not happen, so the text can go
// back into the local queue and out at the end of the turn instead.
type followUpFailedMsg struct {
	text string
	err  error
}

// isSlashCommand reports whether a composed line is a client-side command
// rather than something to say to the agent.
//
// Slash commands are never sent mid-turn: "/clear" folded into a running
// turn's context is a word the model would try to interpret, and the command
// itself means "clear once this turn is over". They keep the local queue.
func isSlashCommand(query string) bool {
	return strings.HasPrefix(strings.TrimSpace(query), "/")
}

// followUpSupported reports whether Enter mid-turn can actually reach the
// running turn: this transport has to speak the call AND the agent on the
// other end has to be new enough to have the endpoint.
func (m ChatModel) followUpSupported() bool {
	sender, ok := m.client.(client.FollowUpSender)
	return ok && sender.FollowUpSupported()
}

// sendFollowUp delivers one mid-turn message to the running turn.
func (m ChatModel) sendFollowUp(text string) tea.Cmd {
	sender, ok := m.client.(client.FollowUpSender)
	if !ok {
		// Unreachable through handleKey, which checks followUpSupported first.
		// Kept as a message rather than a panic so a future caller that skips
		// the check loses the delivery loudly instead of silently.
		return func() tea.Msg {
			return followUpFailedMsg{text: text, err: fmt.Errorf(
				"this agent connection cannot take a message mid-turn")}
		}
	}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), followUpTimeout)
		defer cancel()
		if err := sender.SendFollowUp(ctx, text); err != nil {
			return followUpFailedMsg{text: text, err: err}
		}
		return followUpSentMsg{text: text}
	}
}

// handleFollowUpSent records a delivered mid-turn message.
//
// The transcript line is written HERE, not when Enter was pressed, so what is
// on screen is what the agent actually received. An optimistic line would be
// indistinguishable from a delivered one at the exact moment the difference
// matters — when delivery failed.
func (m ChatModel) handleFollowUpSent(msg followUpSentMsg) (tea.Model, tea.Cmd) {
	m.sending = dropFirst(m.sending, msg.text)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: msg.text})
	// Once per session. "It went to the running turn, not into a queue" is the
	// one thing about this that is not obvious; repeating it under every
	// follow-up would be noise on top of the answer the user is reading.
	if !m.followUpNoted {
		m.followUpNoted = true
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: "sent to the turn already running — " +
				m.agentName + " picks it up at its next step, without restarting what it is doing.",
		})
	}
	m.updateViewport()
	return m, nil
}

// handleFollowUpFailed recovers an undelivered message.
//
// Never silently: the user watched the composer empty, which is this UI's
// promise that the message was taken. If it did not reach the agent they are
// owed both the reason and where it went instead.
//
// Where it goes depends on whether there is still a turn to wait behind. With
// one running, the queue is right — it goes out when that turn ends. With the
// turn already over, the queue would fire it as a new turn IMMEDIATELY, and the
// commonest way to end up here is the user pressing Esc: answering a cancel by
// starting a turn is the opposite of what they asked for. So it goes back to
// the composer, where Esc's own recovery puts everything else.
func (m ChatModel) handleFollowUpFailed(msg followUpFailedMsg) (tea.Model, tea.Cmd) {
	m.sending = dropFirst(m.sending, msg.text)

	landed := "holding it until this turn ends"
	if !m.streaming {
		landed = "put it back in the composer"
	}
	m.messages = append(m.messages, Message{
		Role: RoleStatus,
		Content: fmt.Sprintf("[!] %s — %s.",
			sanitizeErrorText(msg.err.Error()), landed),
	})

	if m.streaming {
		m.queued = append(m.queued, msg.text)
		m.updateViewport()
		return m, nil
	}
	m.restoreToComposer(msg.text)
	m.updateViewport()
	return m, nil
}

// dropFirst removes the first occurrence of text, leaving the rest in order.
// By value, not by index: two deliveries settle in whatever order the network
// answers, and the same text can legitimately be sent twice.
func dropFirst(items []string, text string) []string {
	for i, s := range items {
		if s == text {
			return append(items[:i:i], items[i+1:]...)
		}
	}
	return items
}
