package chat

import (
	"context"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// nullClient satisfies client.AgentClient without doing anything: these tests
// drive the model's event handling directly.
type nullClient struct{ resets int }

func (n *nullClient) Send(context.Context, string) (<-chan interface{}, error) {
	ch := make(chan interface{})
	close(ch)
	return ch, nil
}
func (n *nullClient) Close() error     { return nil }
func (n *nullClient) ResetTranscript() { n.resets++ }

func newTestModel(t *testing.T) (ChatModel, *nullClient) {
	t.Helper()
	c := &nullClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 100, 30
	return m, c
}

// feed runs a sequence of events through the model, as Bubble Tea would: the
// model is copied on every update, which is what makes a value-held
// strings.Builder unusable here.
func feed(t *testing.T, m ChatModel, events ...interface{}) ChatModel {
	t.Helper()
	for _, e := range events {
		updated, _ := m.handleEvent(e)
		m = updated.(ChatModel)
	}
	return m
}

func TestCanonicalStreamedTokensBecomeOneAnswer(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m,
		event.CanonicalStatusEvent{Type: "status", Message: "Scanning inbox"},
		event.CanonicalTokenEvent{Type: "token", Delta: "You have "},
		event.CanonicalTokenEvent{Type: "token", Delta: "3 urgent "},
		event.CanonicalTokenEvent{Type: "token", Delta: "emails."},
		event.CanonicalFinalEvent{
			Type:   "final",
			Answer: "You have 3 urgent emails.",
			Usage:  []byte(`{"steps":2,"tools_used":1}`),
		},
	)

	if m.streaming {
		t.Error("a terminal `final` must end the turn")
	}
	var answers []Message
	for _, msg := range m.messages {
		if msg.Role == RoleAssistant {
			answers = append(answers, msg)
		}
	}
	if len(answers) != 1 {
		t.Fatalf("expected exactly 1 assistant message, got %d: %+v", len(answers), m.messages)
	}
	if answers[0].Content != "You have 3 urgent emails." {
		t.Errorf("answer = %q", answers[0].Content)
	}
	if answers[0].Steps != 2 || answers[0].ToolsUsed != 1 {
		t.Errorf("usage not carried into the message: %+v", answers[0])
	}
}

func TestCanonicalFinalWithoutTokens(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "no streaming here"})

	last := m.messages[len(m.messages)-1]
	if last.Role != RoleAssistant || last.Content != "no streaming here" {
		t.Fatalf("unexpected message: %+v", last)
	}
}

func TestCanonicalToolCallAndResult(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "search_email", Args: []byte(`{"query":"invoice"}`)},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "search_email",
			Render: "table", Data: []byte(`{"ok":true,"columns":["from"],"rows":[["a@b.c"]]}`),
		},
	)

	if len(m.activity) != 1 {
		t.Fatalf("expected one tool activity line, got %+v", m.activity)
	}
	item := m.activity[0]
	if !item.Done {
		t.Error("the tool line must be marked done by its result")
	}
	if item.Success == nil || !*item.Success {
		t.Errorf("expected success from {\"ok\":true}, got %v", item.Success)
	}
	if !strings.Contains(item.Content, "search_email") || !strings.Contains(item.Content, "invoice") {
		t.Errorf("tool line lost its detail: %q", item.Content)
	}
	// The render key is no longer echoed onto the activity line — it now draws a
	// real card in the transcript, which is where the detail belongs.
	if strings.Contains(item.Content, "render:") {
		t.Errorf("tool line still echoes the raw render key: %q", item.Content)
	}
	var card *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleCard {
			card = &m.messages[i]
		}
	}
	if card == nil {
		t.Fatal("render=table produced no card message")
	}
	if card.Render != "table" {
		t.Errorf("card render = %q, want table", card.Render)
	}
	if rendered := m.renderMessage(*card); !strings.Contains(rendered, "a@b.c") {
		t.Errorf("table card did not draw its row:\n%s", rendered)
	}
}

func TestCanonicalToolResultFailureIsMarkedFailed(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m,
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "send_draft"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "send_draft", Data: []byte(`{"ok":false}`)},
	)

	item := m.activity[0]
	if item.Success == nil || *item.Success {
		t.Errorf("expected failure from {\"ok\":false}, got %v", item.Success)
	}
}

// needs_confirmation must be visible and must NOT end the turn — the sidecar
// sends its own terminal event right after.
func TestCanonicalNeedsConfirmationIsSurfacedAndTurnContinues(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m, event.CanonicalNeedsConfirmationEvent{
		Type: "needs_confirmation", RunID: "abc",
		Action: "send_draft", Summary: "Send reply to alice@example.com",
	})

	if !m.streaming {
		t.Error("needs_confirmation must not end the turn on its own")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus {
		t.Fatalf("unexpected role %v", last.Role)
	}
	if !strings.Contains(last.Content, "send_draft") || !strings.Contains(last.Content, "alice@example.com") {
		t.Errorf("the pending action must be readable: %q", last.Content)
	}

	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "Skipped — needs approval."})
	if m.streaming {
		t.Error("the following `final` must end the turn")
	}
}

func TestCanonicalErrorEndsTurnAndKeepsPartialText(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m,
		event.CanonicalTokenEvent{Type: "token", Delta: "partial answer"},
		event.CanonicalErrorEvent{Type: "error", Detail: "Lemonade Server is not reachable — run `gaia init`", Status: 503},
	)

	if m.streaming {
		t.Error("a terminal `error` must end the turn")
	}
	var sawPartial, sawError bool
	for _, msg := range m.messages {
		if msg.Role == RoleAssistant && msg.Content == "partial answer" {
			sawPartial = true
		}
		if msg.Role == RoleError && strings.Contains(msg.Content, "gaia init") {
			sawError = true
		}
	}
	if !sawPartial {
		t.Error("streamed text must not be discarded when the run errors")
	}
	if !sawError {
		t.Errorf("the actionable detail must be surfaced verbatim: %+v", m.messages)
	}
}

func TestCanonicalUnsupportedAndMalformedAreVisible(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m,
		event.CanonicalUnsupportedEvent{EventType: "needs_input", Raw: `{"type":"needs_input"}`},
		event.CanonicalMalformedEvent{Payload: `{"type":"token"`, Reason: "not valid JSON: unexpected end"},
	)

	if !m.streaming {
		t.Error("neither event terminates the run")
	}
	if len(m.messages) != 2 {
		t.Fatalf("both events must be shown, got %+v", m.messages)
	}
	if !strings.Contains(m.messages[0].Content, "needs_input") {
		t.Errorf("unsupported event not named: %q", m.messages[0].Content)
	}
	if !strings.Contains(m.messages[1].Content, "not valid JSON") {
		t.Errorf("malformed reason not shown: %q", m.messages[1].Content)
	}
}

// The legacy in-process vocabulary must keep working for the subprocess transport.
func TestLegacyEventsStillHandled(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true

	m = feed(t, m,
		event.StepEvent{Type: "step", Step: 1, Total: 3, Status: "running"},
		event.ThinkingEvent{Type: "thinking", Content: "let me look"},
		event.AnswerEvent{Type: "answer", Content: "legacy answer", Steps: 1, ToolsUsed: 0},
	)

	if m.streaming {
		t.Error("a legacy answer must end the turn")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleAssistant || last.Content != "legacy answer" {
		t.Fatalf("unexpected message: %+v", last)
	}
}
