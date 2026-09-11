package chat

import (
	"context"
	"errors"
	"strings"
	"testing"
)

type clearClient struct {
	nullClient
	err    error
	clears int
}

func (c *clearClient) ClearConversation(context.Context) error { c.clears++; return c.err }

func TestClearWaitsForAgentAcknowledgment(t *testing.T) {
	for _, fail := range []bool{false, true} {
		c := &clearClient{}
		if fail {
			c.err = errors.New("agent refused")
		}
		m := NewChatModel(c, "gaia", "GAIA", false)
		m.messages = []Message{{Role: RoleUser, Content: "prior instruction"}}
		next, cmd := m.submit("/clear")
		m = next.(ChatModel)
		if !m.streaming || len(m.messages) != 1 {
			t.Fatal("history cleared before acknowledgment")
		}
		if c.clears != 0 {
			t.Fatal("reset blocked the UI update")
		}
		updated, _ := m.Update(cmd())
		m = updated.(ChatModel)
		if m.streaming || c.clears != 1 {
			t.Fatal("reset did not settle")
		}
		if fail {
			if len(m.messages) != 2 || !strings.Contains(m.messages[1].Content, "agent refused") {
				t.Fatalf("failure erased history or was hidden: %+v", m.messages)
			}
		} else if len(m.messages) != 0 {
			t.Fatal("acknowledged reset did not clear history")
		}
	}
}
