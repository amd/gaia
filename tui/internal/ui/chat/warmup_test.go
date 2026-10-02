// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
)

// warmClient is the flagship's stdio transport as the chat sees it: it takes
// the warm-up sentinel and mid-turn follow-ups.
type warmClient struct {
	followUpClient
	queries []string
}

func (c *warmClient) SupportsWarmUp() bool { return true }

func (c *warmClient) Send(_ context.Context, q string) (<-chan interface{}, error) {
	c.queries = append(c.queries, q)
	return make(chan interface{}), nil
}

func warmModel(t *testing.T) (ChatModel, *warmClient) {
	t.Helper()
	c := &warmClient{followUpClient: followUpClient{supported: true}}
	m := NewChatModel(c, "gaia", "", false)
	m.width, m.height = 100, 30
	m.resize()
	updated, cmd := m.Update(startWarmUpMsg{})
	sendsIn(cmd)
	return updated.(ChatModel), c
}

// sendsIn runs the turn's send, skipping the spinner tick batched beside it.
func sendsIn(cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	if batch, ok := cmd().(tea.BatchMsg); ok {
		for _, c := range batch[1:] {
			if c != nil {
				c()
			}
		}
	}
}

func frameText(m ChatModel) string { return ansi.Strip(visibleFrame(m)) }

// The warm-up is its own stage: it names what it is doing and replaces the
// transcript, instead of the first question sitting on a bare spinner.
func TestTheWarmUpIsAStageBeforeChat(t *testing.T) {
	m, c := warmModel(t)
	if !m.warming || len(c.queries) != 1 || c.queries[0] != client.WarmUpQuery {
		t.Fatalf("warming=%t queries=%q — want the warm-up sentinel sent", m.warming, c.queries)
	}
	frame := frameText(m)
	t.Logf("\n%s", frame)
	for _, want := range []string{"Getting GAIA ready", "Starting the agent", "Esc to show the chat",
		"getting ready", "Esc show chat", "Ctrl+C quit"} {
		if !strings.Contains(frame, want) {
			t.Errorf("stage missing %q", want)
		}
	}
	if strings.Contains(frame, "Welcome to GAIA") {
		t.Error("the transcript is showing behind the stage")
	}

	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Indexing tools"},
		event.CanonicalStatusEvent{Type: "status", Message: "Loading Qwen3 and reading its instructions"})
	frame = frameText(m)
	for _, want := range []string{"✓ Starting the agent", "✓ Indexing tools", "Loading Qwen3 and reading its instructions"} {
		if !strings.Contains(frame, want) {
			t.Errorf("stage missing %q:\n%s", want, frame)
		}
	}
}

// The sentinel's answer ends the stage without appearing as an answer.
func TestAFinishedWarmUpOpensTheChat(t *testing.T) {
	m, _ := warmModel(t)
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: client.WarmedUp})
	if m.warming || m.streaming {
		t.Fatalf("warming=%t streaming=%t after the warm-up finished", m.warming, m.streaming)
	}
	for _, msg := range m.messages {
		if msg.Role == RoleAssistant {
			t.Errorf("the sentinel's answer was shown as a reply: %q", msg.Content)
		}
	}
	last := m.messages[len(m.messages)-1]
	if !strings.Contains(last.Content, "Ready") {
		t.Errorf("last message = %q, want the ready line", last.Content)
	}
	if strings.Contains(frameText(m), "Getting GAIA ready") {
		t.Error("the stage is still on screen")
	}
}

// A remote model has nothing to load: the stage ends without a word.
func TestASkippedWarmUpLeavesNoTrace(t *testing.T) {
	m, _ := warmModel(t)
	before := len(m.messages)
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: client.WarmUpSkipped})
	if m.warming || len(m.messages) != before {
		t.Errorf("warming=%t, messages %d -> %d", m.warming, before, len(m.messages))
	}
}

// A failed warm-up says so and still opens the chat.
func TestAFailedWarmUpIsReportedAndOpensTheChat(t *testing.T) {
	m, _ := warmModel(t)
	m = feed(t, m, event.CanonicalErrorEvent{Type: "error",
		Detail: "Could not get the model ready ahead of time — chat still works"})
	if m.warming || m.streaming {
		t.Fatalf("warming=%t streaming=%t after a failed warm-up", m.warming, m.streaming)
	}
	if last := m.messages[len(m.messages)-1]; last.Role != RoleError {
		t.Errorf("the failure was not shown: %+v", last)
	}
}

// Esc shows the chat but does not cancel: on this transport a cancel kills the
// agent, throwing away the work being done.
func TestEscShowsTheChatWithoutCancelling(t *testing.T) {
	m, _ := warmModel(t)
	cancelled := false
	m.cancelFn = func() { cancelled = true }

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)
	if cancelled || !m.warming || !m.warmHidden {
		t.Fatalf("cancelled=%t warming=%t hidden=%t — want the stage hidden, warm-up still running",
			cancelled, m.warming, m.warmHidden)
	}
	if strings.Contains(frameText(m), "Esc to show the chat") {
		t.Error("the stage is still on screen after Esc")
	}
}

// Typing during the warm-up queues the message for when the agent is ready —
// never a mid-turn follow-up into the sentinel's "turn".
func TestAMessageTypedDuringWarmUpWaitsForIt(t *testing.T) {
	m, c := warmModel(t)
	m.input.SetValue("what's on my calendar?")
	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)
	if len(c.sent) != 0 {
		t.Fatalf("sent %q into the warm-up turn", c.sent)
	}
	if len(m.queued) != 1 {
		t.Fatalf("queued = %q, want the message held", m.queued)
	}
	frame := frameText(m)
	if !strings.Contains(frame, "sent when GAIA is ready") || strings.Contains(frame, "Esc stops the turn") {
		t.Errorf("the queued row must not offer an Esc that does something else here:\n%s", frame)
	}
}

// Only the flagship over its own transport gets a warm-up; any other agent
// would receive the sentinel as a question.
func TestOnlyTheFlagshipWarmsUp(t *testing.T) {
	c := &warmClient{}
	for _, id := range []string{"email", "gaia"} {
		m := NewChatModel(c, id, "", false)
		if got := m.warmUpApplies(); got != (id == "gaia") {
			t.Errorf("warmUpApplies for %q = %t", id, got)
		}
	}
	if NewChatModel(&nullClient{}, "gaia", "", false).warmUpApplies() {
		t.Error("a transport without the sentinel must not warm up")
	}
	if NewChatModel(c, "gaia", "hello", false).warmUpApplies() {
		t.Error("a launch with a question already pays for the same work")
	}
}

// A fresh session opens on a few things to try, not a blank screen.
func TestAFreshSessionOpensOnWhatToTry(t *testing.T) {
	m, _ := warmModel(t)
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: client.WarmedUp})
	if !strings.Contains(frameText(m), "Try: ") {
		t.Errorf("no starting hint on a fresh session:\n%s", frameText(m))
	}
}

// Someone who already typed a question does not need the hint.
func TestNoHintWhenAQuestionIsAlreadyWaiting(t *testing.T) {
	m, _ := warmModel(t)
	m.input.SetValue("what's on my calendar?")
	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = feed(t, updated.(ChatModel), event.CanonicalFinalEvent{Type: "final", Answer: client.WarmedUp})
	for _, msg := range m.messages {
		if strings.Contains(msg.Content, firstPromptHint) {
			t.Fatalf("hint shown although a question was queued: %q", msg.Content)
		}
	}
}

// Every row of a long status line keeps the transcript's indent.
func TestAWrappedStatusStaysIndented(t *testing.T) {
	m, _ := warmModel(t)
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: client.WarmedUp})
	rendered := ansi.Strip(m.renderMessage(&m.messages[len(m.messages)-1], map[string]bool{}))
	for _, row := range strings.Split(rendered, "\n") {
		if strings.TrimSpace(row) != "" && !strings.HasPrefix(row, "  ") {
			t.Errorf("row lost its indent: %q", row)
		}
	}
}
