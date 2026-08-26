package event

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestParseCanonicalStatus(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"status","message":"Scanning inbox"}`))
	s, ok := e.(CanonicalStatusEvent)
	if !ok {
		t.Fatalf("expected CanonicalStatusEvent, got %T", e)
	}
	if s.Message != "Scanning inbox" {
		t.Errorf("message = %q", s.Message)
	}
}

func TestParseCanonicalToken(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"token","delta":"Hel"}`))
	tok, ok := e.(CanonicalTokenEvent)
	if !ok {
		t.Fatalf("expected CanonicalTokenEvent, got %T", e)
	}
	if tok.Delta != "Hel" {
		t.Errorf("delta = %q", tok.Delta)
	}
}

func TestParseCanonicalToolCall(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"tool_call","tool":"search_email","args":{"query":"invoice"}}`))
	tc, ok := e.(CanonicalToolCallEvent)
	if !ok {
		t.Fatalf("expected CanonicalToolCallEvent, got %T", e)
	}
	if tc.Tool != "search_email" {
		t.Errorf("tool = %q", tc.Tool)
	}
	var args map[string]string
	if err := json.Unmarshal(tc.Args, &args); err != nil {
		t.Fatalf("args: %v", err)
	}
	if args["query"] != "invoice" {
		t.Errorf("args[query] = %q", args["query"])
	}
}

func TestParseCanonicalToolResultCarriesRenderAndData(t *testing.T) {
	line := []byte(`{"type":"tool_result","tool":"pre_scan_inbox","render":"email_pre_scan","data":{"urgent":[{"subject":"Wire transfer"}]}}`)
	e := ParseCanonicalEvent(line)
	tr, ok := e.(CanonicalToolResultEvent)
	if !ok {
		t.Fatalf("expected CanonicalToolResultEvent, got %T", e)
	}
	if tr.Tool != "pre_scan_inbox" || tr.Render != "email_pre_scan" {
		t.Errorf("unexpected values: %+v", tr)
	}
	if !strings.Contains(string(tr.Data), "Wire transfer") {
		t.Errorf("data not carried through: %s", tr.Data)
	}
}

func TestParseCanonicalToolResultWithoutRender(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"tool_result","tool":"list_labels","data":{"ok":true}}`))
	tr, ok := e.(CanonicalToolResultEvent)
	if !ok {
		t.Fatalf("expected CanonicalToolResultEvent, got %T", e)
	}
	if tr.Render != "" {
		t.Errorf("render should be empty, got %q", tr.Render)
	}
}

func TestParseCanonicalNeedsConfirmation(t *testing.T) {
	line := []byte(`{"type":"needs_confirmation","run_id":"7b1e","action":"send_draft","summary":"Send reply to alice@example.com"}`)
	e := ParseCanonicalEvent(line)
	nc, ok := e.(CanonicalNeedsConfirmationEvent)
	if !ok {
		t.Fatalf("expected CanonicalNeedsConfirmationEvent, got %T", e)
	}
	if nc.RunID != "7b1e" || nc.Action != "send_draft" {
		t.Errorf("unexpected values: %+v", nc)
	}
	if nc.ConfirmURL != "" {
		t.Errorf("confirm_url should be absent under the stateless model, got %q", nc.ConfirmURL)
	}
}

func TestParseCanonicalFinalWithUsage(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"final","answer":"3 urgent emails","usage":{"steps":4,"tools_used":2,"elapsed":8.5,"ttft":1.2}}`))
	f, ok := e.(CanonicalFinalEvent)
	if !ok {
		t.Fatalf("expected CanonicalFinalEvent, got %T", e)
	}
	if f.Answer != "3 urgent emails" {
		t.Errorf("answer = %q", f.Answer)
	}
	usage := CanonicalUsageOf(f)
	if usage.Steps != 4 || usage.ToolsUsed != 2 || usage.Elapsed != 8.5 || usage.TTFT != 1.2 {
		t.Errorf("unexpected usage: %+v", usage)
	}
	if CanonicalTerminalType(f) != CanonicalTypeFinal {
		t.Error("final must be terminal")
	}
}

func TestCanonicalUsageOfMissingOrBad(t *testing.T) {
	if u := CanonicalUsageOf(CanonicalFinalEvent{Type: "final", Answer: "x"}); u != (CanonicalUsage{}) {
		t.Errorf("absent usage should be zero, got %+v", u)
	}
	bad := CanonicalFinalEvent{Type: "final", Usage: json.RawMessage(`"not-an-object"`)}
	if u := CanonicalUsageOf(bad); u != (CanonicalUsage{}) {
		t.Errorf("unreadable usage should be zero, got %+v", u)
	}
}

func TestParseCanonicalError(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"error","detail":"Lemonade Server is not reachable","status":503}`))
	ev, ok := e.(CanonicalErrorEvent)
	if !ok {
		t.Fatalf("expected CanonicalErrorEvent, got %T", e)
	}
	if ev.Detail != "Lemonade Server is not reachable" || ev.Status != 503 {
		t.Errorf("unexpected values: %+v", ev)
	}
	if CanonicalTerminalType(ev) != CanonicalTypeError {
		t.Error("error must be terminal")
	}
}

// Contract §7: an unknown top-level type is surfaced, never dropped.
func TestParseCanonicalUnknownTypeIsVisible(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"needs_telepathy","prompt":"Which mailbox?"}`))
	u, ok := e.(CanonicalUnsupportedEvent)
	if !ok {
		t.Fatalf("expected CanonicalUnsupportedEvent, got %T", e)
	}
	if u.EventType != "needs_telepathy" {
		t.Errorf("event type = %q", u.EventType)
	}
	if !strings.Contains(u.Raw, "Which mailbox?") {
		t.Errorf("raw payload not retained: %q", u.Raw)
	}
}

// needs_input carries the options AND their descriptions: a label alone does not
// tell the user what picking it will do.
func TestParseCanonicalNeedsInput(t *testing.T) {
	e := ParseCanonicalEvent([]byte(`{"type":"needs_input","run_id":"r","request_id":"q1",` +
		`"question":"Which mailbox should I connect?",` +
		`"options":[{"value":"google","label":"Gmail","description":"A gmail.com account."},` +
		`{"value":"microsoft","label":"Outlook"}],` +
		`"allow_free_text":false,"respond_url":"/v1/email/query/r/respond","timeout_seconds":240}`))
	ev, ok := e.(CanonicalNeedsInputEvent)
	if !ok {
		t.Fatalf("expected CanonicalNeedsInputEvent, got %T", e)
	}
	if ev.RequestID != "q1" || ev.Question != "Which mailbox should I connect?" {
		t.Errorf("unexpected values: %+v", ev)
	}
	if len(ev.Options) != 2 {
		t.Fatalf("options = %d, want 2", len(ev.Options))
	}
	if ev.Options[0].Description != "A gmail.com account." {
		t.Errorf("description lost: %+v", ev.Options[0])
	}
	if ev.AllowFreeText {
		t.Error("allow_free_text must survive as false")
	}
	if ev.TimeoutSeconds != 240 {
		t.Errorf("timeout = %d", ev.TimeoutSeconds)
	}
	// The run continues after a question — it is not a terminal event.
	if CanonicalTerminalType(ev) != "" {
		t.Error("needs_input must not terminate the run")
	}
}

func TestParseCanonicalMalformedIsVisible(t *testing.T) {
	cases := []struct {
		name string
		in   string
	}{
		{"not json", `{"type":"token"`},
		{"no type", `{"message":"orphan"}`},
		{"wrong field type", `{"type":"token","delta":42}`},
		{"empty", ``},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			e := ParseCanonicalEvent([]byte(tc.in))
			m, ok := e.(CanonicalMalformedEvent)
			if !ok {
				t.Fatalf("expected CanonicalMalformedEvent, got %T", e)
			}
			if m.Reason == "" {
				t.Error("a malformed event must carry a reason")
			}
			if CanonicalTerminalType(e) != "" {
				t.Error("a malformed event must not terminate the run")
			}
		})
	}
}

func TestParseCanonicalTruncatesHugePayload(t *testing.T) {
	huge := `{"type":"nope","blob":"` + strings.Repeat("x", 5000) + `"}`
	u, ok := ParseCanonicalEvent([]byte(huge)).(CanonicalUnsupportedEvent)
	if !ok {
		t.Fatalf("expected CanonicalUnsupportedEvent")
	}
	if len(u.Raw) > maxRawEcho+4 {
		t.Errorf("raw echo not truncated: %d bytes", len(u.Raw))
	}
}

// The legacy in-process vocabulary must keep working — the subprocess transport
// still speaks it.
func TestLegacyParserStillIndependent(t *testing.T) {
	legacy, err := ParseEvent([]byte(`{"type":"answer","content":"hi","steps":1,"tools_used":0}`))
	if err != nil {
		t.Fatalf("legacy ParseEvent: %v", err)
	}
	if _, ok := legacy.(AnswerEvent); !ok {
		t.Fatalf("expected AnswerEvent, got %T", legacy)
	}
	// "answer" is not part of the canonical seven.
	if _, ok := ParseCanonicalEvent([]byte(`{"type":"answer","content":"hi"}`)).(CanonicalUnsupportedEvent); !ok {
		t.Error("legacy `answer` must read as unsupported on the canonical parser")
	}
}
