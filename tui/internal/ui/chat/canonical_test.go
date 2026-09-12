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
	// The token count is a --dev figure; this test is about the plumbing
	// that carries it, so it asks for the dev breakdown explicitly.
	m.dev = true
	m.streaming = true
	m.queryStart = time.Now().Add(-5 * time.Second)

	m = feed(t, m, event.CanonicalFinalEvent{
		Type:   "final",
		Answer: "short",
		Usage:  []byte(`{"steps":2,"tools_used":1,"tokens":42,"ttft":1.0}`),
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
	m.dev = true
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

// TestCanonicalTTFTIsNeverMeasuredClientSide is the regression for the number
// that started this: an 11-step turn printed "2208.3s · ttft 2206.8s", because
// the first token the CLIENT sees on a tool-calling turn arrives only after
// the agent has finished deciding. Nothing on the wire but the backend's own
// measurement may set ttft.
func TestCanonicalTTFTIsNeverMeasuredClientSide(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-2206 * time.Second)

	m = feed(t, m,
		event.CanonicalStatusEvent{Type: "status", Message: "Scanning inbox"},
		event.CanonicalTokenEvent{Type: "token", Delta: "Hi"},
		event.CanonicalTokenEvent{Type: "token", Delta: " there"},
		event.CanonicalFinalEvent{Type: "final", Answer: "Hi there"},
	)
	if last := m.messages[len(m.messages)-1]; last.TTFT != 0 {
		t.Errorf("TTFT = %v on a turn whose backend reported none, want 0 (omitted)", last.TTFT)
	}
}

// A legacy answer event carrying no measurements leaves them absent, however
// long the turn took on this side.
func TestLegacyTransportWithNoMeasurementsReportsNone(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-5 * time.Second)

	m = feed(t, m, event.AnswerEvent{Type: "answer", Content: "nothing measured", Steps: 1})

	last := m.messages[len(m.messages)-1]
	if last.TTFT != 0 || last.TokPerS != 0 || last.Tokens != 0 {
		t.Errorf("invented a measurement nobody reported: %+v", last)
	}
}

// ...and one that carries them renders them, same as the daemon transport.
// The producer has always put these on the answer event; the legacy path used
// to drop all three, so a subprocess run showed none of them.
func TestLegacyTransportRendersTheBackendsMeasurements(t *testing.T) {
	m, _ := newTestModel(t)
	m.dev = true
	m.streaming = true
	m.queryStart = time.Now().Add(-37 * time.Second)

	m = feed(t, m, event.AnswerEvent{
		Type: "answer", Content: "391", Steps: 1,
		Tokens: 68, TTFT: 31.695, TokPerS: 13.3,
	})

	last := m.messages[len(m.messages)-1]
	rendered := m.renderMessage(&last, nil)
	for _, want := range []string{"ttft 31.7s", "68 tokens", "13.3 tok/s"} {
		if !strings.Contains(rendered, want) {
			t.Errorf("stats line missing %q:\n%s", want, rendered)
		}
	}
}

// The backend's ttft reaches the user on the non-streaming tool-calling path —
// which is the normal one for a native tool-calling model, and the path where
// the old client-side stamp was most wrong.
func TestCanonicalServerReportedTTFTReachesTheUser(t *testing.T) {
	m, _ := newTestModel(t)
	m.dev = true
	m.streaming = true
	m.queryStart = time.Now().Add(-82 * time.Second)

	// No CanonicalTokenEvent anywhere in this turn — the non-streaming
	// daemon path a native tool-calling model always takes.
	m = feed(t, m, event.CanonicalFinalEvent{
		Type:   "final",
		Answer: "triage summary",
		Usage:  []byte(`{"steps":2,"tools_used":1,"tokens":72,"ttft":9.4}`),
	})

	last := m.messages[len(m.messages)-1]
	wantTTFT := time.Duration(9.4 * float64(time.Second))
	if last.TTFT != wantTTFT {
		t.Fatalf("TTFT = %v, want the backend-reported %v", last.TTFT, wantTTFT)
	}

	rendered := m.renderMessage(&last, nil)
	if !strings.Contains(rendered, "ttft 9.4s") {
		t.Errorf("rendered stats line missing \"ttft 9.4s\" — ttft still never reaches the user:\n%s", rendered)
	}
}

// TestCanonicalServerReportedTTFTWinsOverAStreamedTurn: a turn that streamed
// tokens still takes its ttft from the backend. The client's own wall clock
// looks like the more complete measurement — it covers the wire too — but on
// any turn with tool calls it is measuring the agent loop, not the model.
func TestCanonicalServerReportedTTFTWinsOverAStreamedTurn(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-8 * time.Second)

	m = feed(t, m,
		event.CanonicalTokenEvent{Type: "token", Delta: "Hi"},
		event.CanonicalFinalEvent{
			Type:   "final",
			Answer: "Hi there",
			Usage:  []byte(`{"ttft":0.05,"tok_per_s":42.5}`),
		},
	)

	last := m.messages[len(m.messages)-1]
	if last.TTFT != 50*time.Millisecond {
		t.Errorf("TTFT = %v, want the backend-reported 50ms", last.TTFT)
	}
	if last.TokPerS != 42.5 {
		t.Errorf("TokPerS = %v, want the backend-reported 42.5", last.TokPerS)
	}
}

// A backend that reports no rate must leave the stat absent rather than have
// the client divide tokens by a wall clock that counted tool time.
func TestCanonicalNoReportedRateMeansNoRate(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-30 * time.Second)

	m = feed(t, m, event.CanonicalFinalEvent{
		Type:   "final",
		Answer: "done",
		Usage:  []byte(`{"tokens":420,"steps":11}`),
	})

	last := m.messages[len(m.messages)-1]
	if last.TokPerS != 0 {
		t.Errorf("TokPerS = %v on a backend that reported none, want 0 (omitted)", last.TokPerS)
	}
	if last.Tokens != 420 {
		t.Errorf("Tokens = %d, want the reported 420", last.Tokens)
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
	// The line names the work in words, plus the one argument that says what the
	// work is ABOUT. The raw tool name stays on the item for repeat-folding, but
	// it is not what the user reads.
	if !strings.Contains(item.Content, "invoice") {
		t.Errorf("tool line lost the argument that says what it is doing: %q", item.Content)
	}
	if item.Tool != "search_email" {
		t.Errorf("tool line lost the raw tool name it folds on: %q", item.Tool)
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
			switch v := tt.event.(type) {
			case errMsg:
				// errMsg{} as written above has turnSeq's zero value, which
				// only matches a fresh model's own zero-valued turnSeq by
				// coincidence — not what a real errMsg looks like (a live
				// turn's turnSeq is always >= 1, see its doc comment). Drive
				// a real turn first so this exercises turnSeq scoping
				// honestly, matching production (#2912 review).
				updated, _ := m.Update(sendQueryMsg{query: "x"})
				m = updated.(ChatModel)
				updated, _ = m.Update(errMsg{err: v.err, turnSeq: m.turnSeq})
				m = updated.(ChatModel)
			case questionFailedMsg, confirmActionResultMsg:
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
