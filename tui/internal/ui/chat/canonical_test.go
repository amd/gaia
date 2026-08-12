package chat

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

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
	if last.Tokens != 0 {
		t.Errorf("no usage.tokens on the wire -> Message.Tokens must stay 0, got %d", last.Tokens)
	}
}

// TestCanonicalFinalCarriesRealTokenCount is AC2(a): a real usage.tokens
// value on the wire reaches Message.Tokens, and renders as "N tokens" — no
// "~" prefix, since this is a real count, not the old char-length guess.
func TestCanonicalFinalCarriesRealTokenCount(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-5 * time.Second)
	m.ttft = 1 * time.Second

	m = feed(t, m, event.CanonicalFinalEvent{
		Type:   "final",
		Answer: "short",
		Usage:  []byte(`{"steps":2,"tools_used":1,"tokens":42}`),
	})

	last := m.messages[len(m.messages)-1]
	if last.Tokens != 42 {
		t.Fatalf("Tokens = %d, want 42", last.Tokens)
	}

	rendered := m.renderMessage(&last, nil)
	if !strings.Contains(rendered, "42 tokens") {
		t.Errorf("rendered stats line missing \"42 tokens\":\n%s", rendered)
	}
	if strings.Contains(rendered, "~42") {
		t.Errorf("rendered stats line still shows the old approximation marker:\n%s", rendered)
	}
}

// TestCanonicalRenderOmitsTokensWhenZero is AC4: the stats line stays
// present (duration/ttft/steps/tools) even when no real token count exists —
// this is a fix, not a removal, and there is no fallback to the old guess.
func TestCanonicalRenderOmitsTokensWhenZero(t *testing.T) {
	m, _ := newTestModel(t)
	msg := &Message{
		Role:      RoleAssistant,
		Duration:  3200 * time.Millisecond,
		TTFT:      800 * time.Millisecond,
		Steps:     2,
		ToolsUsed: 1,
		Tokens:    0,
		Content:   "a reasonably long answer that would have guessed a nonzero token count under the old code",
	}

	rendered := m.renderMessage(msg, nil)
	if strings.Contains(rendered, "tokens") || strings.Contains(rendered, "tok/s") {
		t.Errorf("expected no tokens/tok-per-sec sub-line when Tokens == 0:\n%s", rendered)
	}
	for _, want := range []string{"3.2s", "ttft 0.8s", "2 steps", "1 tools"} {
		if !strings.Contains(rendered, want) {
			t.Errorf("expected stats line to still contain %q:\n%s", want, rendered)
		}
	}
}

// TestCanonicalTTFTAnchorsOnFirstToken reproduces the WARM-query shape
// measured against a live sidecar (#2899): the status frame arrives
// essentially immediately, but real generation doesn't start for several
// more seconds. Before the fix, ttft was set on the status frame and read
// ~0s in this exact shape — a cold-load-only test (large gap before even the
// status frame) would not catch that regression, since the old code happened
// to look right by coincidence when the delay came before the first frame.
func TestCanonicalTTFTAnchorsOnFirstToken(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now()

	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Scanning inbox"})
	if m.ttft != 0 {
		t.Fatalf("status frame must not set ttft, got %v", m.ttft)
	}

	// Simulate 8s of real elapsed time between the status frame and the
	// first token, matching the live-baseline warm-query gap.
	m.queryStart = m.queryStart.Add(-8 * time.Second)

	m = feed(t, m, event.CanonicalTokenEvent{Type: "token", Delta: "Hi"})
	if m.ttft < 7500*time.Millisecond || m.ttft > 8500*time.Millisecond {
		t.Errorf("ttft = %v, want ~8s (anchored on the token, not the earlier status frame)", m.ttft)
	}

	// A second token must not move ttft again.
	firstTTFT := m.ttft
	m.queryStart = m.queryStart.Add(-100 * time.Second) // would blow up ttft if re-anchored
	m = feed(t, m, event.CanonicalTokenEvent{Type: "token", Delta: " there"})
	if m.ttft != firstTTFT {
		t.Errorf("ttft changed on a second token: got %v, want unchanged %v", m.ttft, firstTTFT)
	}
}

// TestCanonicalLegacyTransportNeverSetsTTFT locks in a deliberate decision:
// the legacy subprocess transport's ChunkEvent is documented as "disabled in
// v1 json-events mode" (types.go), so a legacy AnswerEvent with no preceding
// ChunkEvent leaves ttft at 0 and the stats line omits it entirely — a
// strict improvement over the old "any first frame" anchor, which showed a
// wrong non-zero value there. This is not a bug to fix; this test exists so
// a future reader doesn't mistake the omission for one.
func TestCanonicalLegacyTransportNeverSetsTTFT(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-5 * time.Second)

	m = feed(t, m, event.AnswerEvent{Type: "answer", Content: "no chunk events here", Steps: 1, ToolsUsed: 0})

	last := m.messages[len(m.messages)-1]
	if last.TTFT != 0 {
		t.Errorf("legacy transport with no ChunkEvent must leave TTFT at 0, got %v", last.TTFT)
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
	if rendered := m.renderMessage(card, nil); !strings.Contains(rendered, "a@b.c") {
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

func TestRoleErrorProducersSanitizeControlBytesPreserveNewlines(t *testing.T) {
	malicious := "first\tvalue\r\nsecond\x1b]52;c;Y2xpcGJvYXJk\x07third\x1b[31mred\x1b[0m\x7f"
	tests := []struct {
		name  string
		event interface{}
	}{
		{"canonical error", event.CanonicalErrorEvent{Type: "error", Detail: malicious}},
		{"legacy agent error", event.AgentErrorEvent{Type: "agent_error", Content: malicious}},
		{"legacy error", event.ErrorEvent{Type: "error", Content: malicious}},
		{"transport error", errMsg{err: errors.New(malicious)}},
		{"question delivery error", questionFailedMsg{err: errors.New(malicious)}},
		{"confirmation delivery error", confirmActionResultMsg{Action: "send_draft", err: errors.New(malicious)}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			m, _ := newTestModel(t)
			switch tt.event.(type) {
			case errMsg, questionFailedMsg, confirmActionResultMsg:
				updated, _ := m.Update(tt.event)
				m = updated.(ChatModel)
			default:
				updated, _ := m.handleEvent(tt.event)
				m = updated.(ChatModel)
			}
			last := m.messages[len(m.messages)-1]
			if last.Role != RoleError {
				t.Fatalf("unexpected role %v", last.Role)
			}
			for _, control := range []rune{'\x1b', '\x07', '\r', '\t', '\x7f'} {
				if strings.ContainsRune(last.Content, control) {
					t.Errorf("control byte %U reached Message.Content: %q", control, last.Content)
				}
			}
			if !strings.Contains(last.Content, "first value\nsecond") {
				t.Errorf("message text or newline lost during sanitization: %q", last.Content)
			}
		})
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
