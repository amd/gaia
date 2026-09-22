// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/agents"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// nullClient satisfies client.AgentClient without doing anything real —
// these tests only need to observe whether it got closed.
type nullClient struct{ closed bool }

func (c *nullClient) Send(context.Context, string) (<-chan interface{}, error) {
	ch := make(chan interface{})
	close(ch)
	return ch, nil
}
func (c *nullClient) Close() error { c.closed = true; return nil }

// liveChatModel builds a FlagshipModel already sitting in chat with agentID
// as the current agent, backed by a nullClient — the state switchAgent and
// cancelFromGate both act on.
func liveChatModel(t *testing.T, agentID string) (FlagshipModel, *nullClient) {
	t.Helper()
	cat := catalog.NewCatalog()
	agent := cat.Get(agentID)
	if agent == nil {
		t.Fatalf("test setup: catalog has no %q seed", agentID)
	}
	c := &nullClient{}
	chatModel := chat.NewChatModelForFlagship(c, agent.ID, agent.Name, agent.Version, false, true)
	m := NewFlagshipModel(*agent, false).WithCatalog(cat)
	m.chatClient.set(c)
	m.chat = &chatModel
	m.agent = *agent
	m.activeView = viewChat
	return m, c
}

func TestSwitchAgentSameIDIsANoOp(t *testing.T) {
	m, c := liveChatModel(t, catalog.FlagshipID)
	updated, cmd := m.switchAgent(catalog.FlagshipID)
	m = updated.(FlagshipModel)

	if cmd != nil || m.activeView != viewChat || c.closed {
		t.Fatal("switching to the already-running agent must be an in-place no-op")
	}
	msgs := m.chat.Messages()
	if len(msgs) == 0 || !strings.Contains(msgs[len(msgs)-1].Content, "Already running") {
		t.Fatalf("expected an 'already running' note, got %+v", msgs)
	}
}

func TestSwitchAgentUnknownIDReportsAnActionableError(t *testing.T) {
	m, c := liveChatModel(t, catalog.FlagshipID)
	updated, cmd := m.switchAgent("not-a-real-agent")
	m = updated.(FlagshipModel)

	if cmd != nil || m.activeView != viewChat || c.closed {
		t.Fatal("an unknown id must not touch the live session")
	}
	msgs := m.chat.Messages()
	if len(msgs) == 0 || !strings.Contains(msgs[len(msgs)-1].Content, "gaia hub install not-a-real-agent") {
		t.Fatalf("expected the install remedy named verbatim, got %+v", msgs)
	}
}

func TestSwitchAgentBeginsTheGateAndQueuesTheDivider(t *testing.T) {
	m, c := liveChatModel(t, catalog.FlagshipID)
	updated, _ := m.switchAgent("email")
	m = updated.(FlagshipModel)

	if m.activeView != viewPreflight || m.pending == nil || m.pending.ID != "email" {
		t.Fatalf("switching did not re-enter the readiness gate for the new agent: view=%v pending=%v", m.activeView, m.pending)
	}
	if c.closed {
		t.Fatal("the outgoing client must survive until the gate actually passes — the user can still back out")
	}
	if len(m.pendingTranscript) == 0 {
		t.Fatal("no divider was queued for the replacement chat model")
	}
	last := m.pendingTranscript[len(m.pendingTranscript)-1]
	want := "Switched from gaia to email — email does not see the conversation above this line."
	if last.Content != want {
		t.Fatalf("divider text = %q, want %q", last.Content, want)
	}
}

func TestCancelFromGateDuringASwitchReturnsToChatInsteadOfQuitting(t *testing.T) {
	m, c := liveChatModel(t, catalog.FlagshipID)
	updated, _ := m.switchAgent("email")
	m = updated.(FlagshipModel)

	updated, cmd := m.cancelFromGate()
	m = updated.(FlagshipModel)

	if cmd != nil {
		t.Fatal("backing out of a switch must not quit the program")
	}
	if m.activeView != viewChat || m.chat == nil {
		t.Fatal("backing out of a switch must return to the live chat, not leave the gate up")
	}
	if c.closed {
		t.Fatal("a cancelled switch must not have touched the live client")
	}
	if len(m.pendingTranscript) != 0 {
		t.Fatal("a cancelled switch must not leave a queued transcript behind")
	}
	msgs := m.chat.Messages()
	if len(msgs) == 0 || !strings.Contains(msgs[len(msgs)-1].Content, "Switch cancelled") {
		t.Fatalf("expected a cancellation note in the transcript, got %+v", msgs)
	}
}

func TestAgentsSelectedMsgRoutesThroughUpdateIntoSwitchAgent(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	updated, _ := m.Update(agents.SelectedMsg{ID: "email"})
	m = updated.(FlagshipModel)

	if m.activeView != viewPreflight || m.pending == nil || m.pending.ID != "email" {
		t.Fatal("root.Update did not route agents.SelectedMsg into switchAgent")
	}
}

var _ tea.Model = FlagshipModel{}
