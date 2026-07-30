package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// failedPreScanEnvelope is the issue's literal AC-1 fixture: the email
// sidecar's success:true trap around a nested, string-encoded failure.
const failedPreScanEnvelope = `{"summary":"{\"ok\": false, \"error\": \"CONNECTOR_ERROR: All connected mailboxes failed: google: Gmail batch GET returned 429\"}","success":true,"latency_ms":395.0}`

// AC-1 + AC-4: a failed render tool shows the tool's own error instead of a
// broken card, even though the envelope's top-level "success" field lies.
func TestFailedRenderToolShowsErrorNotCard(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{
			Type:   "tool_result",
			Tool:   "pre_scan_inbox",
			Render: "email_pre_scan",
			Data:   json.RawMessage(failedPreScanEnvelope),
		},
	)

	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			t.Fatalf("a failed render tool must not produce a card: %+v", msg)
		}
	}

	var errMsg *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleError {
			errMsg = &m.messages[i]
		}
	}
	if errMsg == nil {
		t.Fatal("no RoleError message was produced for the failed render tool")
	}
	// Asserted on Message.Content, never on rendered output: at the harness's
	// own 80-column width this phrase wraps mid-string (see the wrap-trap
	// note in the plan), so a rendered-text assertion here would fail a
	// correct implementation.
	if !strings.Contains(errMsg.Content, "CONNECTOR_ERROR") ||
		!strings.Contains(errMsg.Content, "All connected mailboxes failed") {
		t.Errorf("tool error text lost the actionable message: %q", errMsg.Content)
	}

	// (c) short, unwrappable negative strings — safe to assert on rendered
	// output via the established idiom (cards_test.go:129).
	m.updateViewport()
	rendered := ansi.Strip(m.viewport.View())
	if strings.Contains(rendered, "Invalid card") {
		t.Errorf("the broken card box must not appear:\n%s", rendered)
	}
	if strings.Contains(rendered, "raw data:") {
		t.Errorf("the raw JSON dump must not appear:\n%s", rendered)
	}
}

// AC-5: the S1 gate. A failed tool with no render key must be untouched by
// this change — this pins the guard against a future "simplification" that
// removes the Render check and reintroduces the proven batch false positive.
func TestFailedNonRenderToolChangesNothing(t *testing.T) {
	data := json.RawMessage(`{"status":"error","error":"boom"}`)
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message_batch"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "archive_message_batch", Data: data},
	)

	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("a non-render tool result must not gain a new RoleError message: %+v", msg)
		}
	}
	// "keeps its current value" — whatever today's markToolDone classifier
	// (toolResultSucceeded) already computes, unchanged by this fix.
	want := toolResultSucceeded(data)
	item := m.activity[0]
	if item.Success == nil || *item.Success != want {
		t.Errorf("tick changed for a non-render tool: got %v, want %v (today's classifier)", item.Success, want)
	}
}

// AC-6: an empty ToolError.Message must not render an empty [!] panel.
func TestFailedRenderToolWithNoDetailShowsNoDetailMessage(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type:   "tool_result",
		Tool:   "pre_scan_inbox",
		Render: "email_pre_scan",
		Data:   json.RawMessage(`{"ok":false}`),
	})

	var errMsg *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleError {
			errMsg = &m.messages[i]
		}
	}
	if errMsg == nil {
		t.Fatal("no RoleError message was produced")
	}
	if strings.TrimSpace(errMsg.Content) == "" {
		t.Fatal("an empty ToolError.Message must not render an empty panel")
	}
	if !strings.Contains(errMsg.Content, "no detail") {
		t.Errorf("expected the message to say the tool reported no detail, got %q", errMsg.Content)
	}
}

// AC-7a: a failed render tool must tick red, not green.
func TestFailedRenderToolTicksFailed(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{
			Type:   "tool_result",
			Tool:   "pre_scan_inbox",
			Render: "email_pre_scan",
			Data:   json.RawMessage(failedPreScanEnvelope),
		},
	)

	if len(m.activity) != 1 {
		t.Fatalf("expected one tool activity line, got %+v", m.activity)
	}
	item := m.activity[0]
	if item.Success == nil || *item.Success {
		t.Errorf("a failed render tool must tick red, got %v", item.Success)
	}
}

// AC-7b: a genuinely silent render payload (neither ok nor error, so
// ToolOutcomeUnknown) keeps today's ✓ tick. Nothing pinned this before.
func TestSilentRenderPayloadStillTicksSucceeded(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "some_tool"},
		event.CanonicalToolResultEvent{
			Type:   "tool_result",
			Tool:   "some_tool",
			Render: "some_future_card",
			Data:   json.RawMessage(`{"latency_ms":12}`),
		},
	)

	item := m.activity[0]
	if item.Success == nil || !*item.Success {
		t.Errorf("a silent payload should keep today's ✓ tick, got %v", item.Success)
	}
	var card *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleCard {
			card = &m.messages[i]
		}
	}
	if card == nil {
		t.Fatal("ToolOutcomeUnknown must still draw a card (AC-3)")
	}
}

// AC-7c: the pre-mortem's proven false positive. A truncated, string-encoded
// batch summary containing a per-item "error" key classifies as Failed even
// though the operation was an ordinary partial success — but because Render
// is empty, this change must leave it untouched (S1). #2723 is the actual
// fix for the classifier; this test only pins that nothing here acts on it.
func TestBatchFalsePositiveStaysHarmless(t *testing.T) {
	truncated := `{\"succeeded\": [\"m1\", \"m2\", \"m3\"], \"failed\": [{\"message_id\": \"m4\", \"error\": \"not fou`
	data := json.RawMessage(`{"summary":"` + truncated + `","success":true}`)

	// Sanity: this fixture really does trip the classifier (otherwise the
	// test below proves nothing about the gate).
	if outcome, _ := event.ToolOutcomeOf(event.CanonicalToolResultEvent{Tool: "archive_message_batch", Data: data}); outcome != event.ToolOutcomeFailed {
		t.Fatalf("fixture setup: expected the classifier to misfire as Failed, got %s", outcome)
	}

	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message_batch"},
		event.CanonicalToolResultEvent{Type: "tool_result", Tool: "archive_message_batch", Data: data},
	)

	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("a non-render tool must not gain a RoleError message even when the classifier misfires: %+v", msg)
		}
		if msg.Role == RoleCard {
			t.Fatalf("a non-render tool must never produce a card: %+v", msg)
		}
	}
	want := toolResultSucceeded(data)
	item := m.activity[0]
	if item.Success == nil || *item.Success != want {
		t.Errorf("tick must stay whatever today's classifier already computes: got %v, want %v", item.Success, want)
	}
}

// AC-8 / D3 / C1: the RoleError sink has no sanitizer of its own — control
// bytes in a tool's own error text must not reach it. A JSON string can
// carry a real ESC byte via a \u001b escape, so this path is live, not
// theoretical.
func TestFailedRenderErrorSanitizesControlBytesPreservesNewlines(t *testing.T) {
	malicious := "line one\n\x1b[31mred\x1b[0m line two\x07 line three"
	encoded, err := json.Marshal(malicious)
	if err != nil {
		t.Fatal(err)
	}
	data := json.RawMessage(`{"ok":false,"error":` + string(encoded) + `}`)

	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox", Render: "email_pre_scan", Data: data,
	})

	var errMsg *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleError {
			errMsg = &m.messages[i]
		}
	}
	if errMsg == nil {
		t.Fatal("no RoleError message was produced")
	}
	if strings.ContainsRune(errMsg.Content, 0x1b) {
		t.Errorf("ESC byte reached Message.Content: %q", errMsg.Content)
	}
	if strings.ContainsRune(errMsg.Content, 0x07) {
		t.Errorf("C0 byte (BEL) reached Message.Content: %q", errMsg.Content)
	}
	if !strings.Contains(errMsg.Content, "\n") {
		t.Errorf("an embedded newline must survive sanitization (the remedy command needs its own line): %q", errMsg.Content)
	}
	if !strings.Contains(errMsg.Content, "line one") || !strings.Contains(errMsg.Content, "line three") {
		t.Errorf("message text lost content during sanitization: %q", errMsg.Content)
	}
}

// AC-9: guards against overfitting to the issue's specific CONNECTOR_ERROR
// string — any failure text must surface verbatim.
func TestFailedRenderErrorNotHardcodedToConnectorError(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "pre_scan_inbox", Render: "email_pre_scan",
		Data: json.RawMessage(`{"error":"disk is full"}`),
	})

	var errMsg *Message
	for i := range m.messages {
		if m.messages[i].Role == RoleError {
			errMsg = &m.messages[i]
		}
	}
	if errMsg == nil {
		t.Fatal("no RoleError message was produced")
	}
	if !strings.Contains(errMsg.Content, "disk is full") {
		t.Errorf("unrelated failure text must surface verbatim: %q", errMsg.Content)
	}
}
