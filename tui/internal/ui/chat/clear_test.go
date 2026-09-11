package chat

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
)

type clearClient struct {
	nullClient
	err    error
	clears int
}

func (c *clearClient) ClearConversation(context.Context) error { c.clears++; return c.err }
func (c *clearClient) SupportsConversationReset() bool         { return true }

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
		batch, ok := cmd().(tea.BatchMsg)
		if !ok || len(batch) != 2 {
			t.Fatal("reset did not schedule progress and acknowledgment")
		}
		updated, _ := m.Update(batch[1]())
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

func TestClearLegacyAndMockTransportsKeepsViewClearAvailable(t *testing.T) {
	// Both --subprocess and --mock use the legacy SubprocessClient constructor.
	for _, mode := range []string{"subprocess", "mock"} {
		t.Run(mode, func(t *testing.T) {
			c := client.NewSubprocessClient("unused-"+mode, nil, false)
			m := NewChatModel(c, "legacy", "Legacy", false)
			m.messages = []Message{{Role: RoleUser, Content: "old visible text"}}
			next, cmd := m.submit("/clear")
			m = next.(ChatModel)
			if cmd != nil || m.streaming || len(m.messages) != 1 {
				t.Fatal("unsupported reset started an agent command or kept the transcript")
			}
			if m.messages[0].Role != RoleStatus || !strings.Contains(m.messages[0].Content, "context is unchanged") {
				t.Fatalf("missing explicit context limitation: %+v", m.messages)
			}
		})
	}
}

func TestClearShowsFreshProgressWhileAwaitingAcknowledgment(t *testing.T) {
	m := NewChatModel(&clearClient{}, "gaia", "GAIA", false)
	m.width, m.height = 100, 30
	m.activity = []ActivityItem{{Kind: "tool", Content: "old work"}}
	m.buffer = "old partial output"
	m.queryStart = time.Now().Add(-time.Hour)
	m.logPeakRows = 20
	m.firstToken, m.totalSteps = true, 12
	started := time.Now()
	next, cmd := m.submit("/clear")
	m = next.(ChatModel)
	if m.queryStart.Before(started) || m.buffer != "" || m.firstToken || m.totalSteps != 0 {
		t.Fatal("clear retained timing or output from the previous turn")
	}
	view := m.View()
	if !strings.Contains(view, "Clearing conversation") || strings.Contains(view, "old work") || strings.Contains(view, "old partial output") {
		t.Fatalf("clear progress missing or stale: %s", view)
	}
	batch, ok := cmd().(tea.BatchMsg)
	if !ok || len(batch) != 2 {
		t.Fatal("missing spinner command")
	}
	if _, ok := batch[0]().(spinner.TickMsg); !ok {
		t.Fatal("clear did not start the spinner")
	}
	m.cancelFn()
}
