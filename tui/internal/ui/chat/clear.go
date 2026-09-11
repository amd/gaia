package chat

import (
	"context"
	"time"

	"github.com/amd/gaia/tui/internal/client"
	tea "github.com/charmbracelet/bubbletea"
)

type conversationClearedMsg struct {
	turnSeq int
	err     error
}

func (m ChatModel) clearConversation() (tea.Model, tea.Cmd) {
	if resetter, ok := m.client.(client.ConversationResetter); ok {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		m.cancelFn = cancel
		m.streaming = true
		m.turnSeq++
		seq := m.turnSeq
		return m, func() tea.Msg {
			defer cancel()
			return conversationClearedMsg{turnSeq: seq, err: resetter.ClearConversation(ctx)}
		}
	}
	if resetter, ok := m.client.(client.TranscriptResetter); ok {
		resetter.ResetTranscript()
		m.messages = nil
	} else {
		m.messages = append(m.messages, Message{Role: RoleError, Content: "This agent connection cannot clear conversation context."})
	}
	m.updateViewport()
	return m, nil
}

func (m ChatModel) handleConversationCleared(msg conversationClearedMsg) (tea.Model, tea.Cmd) {
	if msg.turnSeq != m.turnSeq {
		return m, nil
	}
	m.streaming = false
	m.settleTurn()
	if msg.err != nil {
		m.messages = append(m.messages, Message{Role: RoleError, Content: "Could not clear conversation: " + msg.err.Error()})
	} else {
		m.messages = nil
	}
	m.updateViewport()
	return m, nil
}
