package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// A failed tool that declares no `render` key has no card, so its step row is
// the whole failure surface: the row says it failed, and its outcome carries
// the tool's own error text — remedy included — for the rest of the session.

// transcriptText is the rendered transcript, as the terminal would show it.
func transcriptText(m ChatModel) string {
	m.updateViewport()
	var b strings.Builder
	for i := range m.messages {
		b.WriteString(ansi.Strip(m.renderMessage(&m.messages[i], nil)))
		b.WriteString("\n")
	}
	return b.String()
}

// AC-1: the tool's own error text, remedy and all, is on screen — in the live
// log while the turn runs, not only after it.
func TestFailedNonRenderToolSurfacesItsErrorText(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "send_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "send_message",
			Data: json.RawMessage(`{"ok":false,"error":"CONNECTOR_ERROR: google is not connected.\nRun: gaia connectors connect google"}`),
		},
	)

	got := flat(rowsOf(m.renderLiveRegion()))
	if !strings.Contains(got, "CONNECTOR_ERROR") ||
		!strings.Contains(got, "gaia connectors connect google") {
		t.Errorf("the tool's own remedy was lost: %q", got)
	}
	if !strings.Contains(got, "failed") {
		t.Errorf("a failure must say so in words, not colour alone: %q", got)
	}
}

// AC-1, DURABLE. `final` clears the live log, so the failure has to survive in
// the turn's work record — and it must be the only copy: one failure, one row.
func TestFailedNonRenderToolSurvivesTheEndOfTheTurn(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "archive_message",
			Data: json.RawMessage(`{"status":"error","error":"boom"}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "Sorry, I could not archive it."},
	)
	if len(m.activity) != 0 {
		t.Fatalf("the work log is expected to be cleared by `final`, got %+v", m.activity)
	}
	rendered := transcriptText(m)
	if n := strings.Count(rendered, "boom"); n != 1 {
		t.Errorf("the failure explanation must outlive the turn exactly once, found %d:\n%s", n, rendered)
	}
}

// AC-4: a mid-turn failure the agent recovers from must not read as a failed
// turn — no bordered panel, no separate line per attempt — while the failed
// attempt stays visible on its step.
func TestRecoveredMidTurnFailureStaysInline(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "search_messages"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "search_messages",
			Data: json.RawMessage(`{"ok":false,"error":"rate limited, retrying"}`),
		},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "list_inbox"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "list_inbox",
			Data: json.RawMessage(`{"ok":true,"count":3}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "You have 3 matching emails."},
	)

	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("a recovered mid-turn failure must not draw the full error panel: %+v", msg)
		}
	}
	rendered := transcriptText(m)
	if !strings.Contains(rendered, "rate limited") {
		t.Errorf("the failed attempt vanished:\n%s", rendered)
	}
	if !strings.Contains(rendered, "You have 3 matching emails.") {
		t.Errorf("the recovered answer must still be there:\n%s", rendered)
	}
}

// The step's tick agrees with its outcome. The fixture carries `status:
// "error"` and no top-level ok/success bool, which an older classifier read as
// a pass — a green tick above an error.
func TestFailedNonRenderToolTicksFailed(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "archive_message",
			Data: json.RawMessage(`{"status":"error","error":"mailbox is read-only"}`),
		},
	)
	if len(m.activity) != 1 {
		t.Fatalf("expected one tool activity line, got %+v", m.activity)
	}
	if item := m.activity[0]; item.Success == nil || *item.Success {
		t.Errorf("a failed non-render tool must tick red, got %v", item.Success)
	}
}

// AC-3: agent-supplied error text cannot move the cursor or restyle the
// terminal from the work log.
func TestFailedNonRenderErrorSanitizesControlBytes(t *testing.T) {
	malicious := "archived 5\tfailed 2\r\nline three\n\x1b[31mred\x1b[0m line four\x07 line five"
	encoded, err := json.Marshal(malicious)
	if err != nil {
		t.Fatal(err)
	}
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "archive_message",
			Data: json.RawMessage(`{"ok":false,"error":` + string(encoded) + `}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "done"},
	)

	for _, msg := range m.messages {
		for _, item := range msg.Work {
			for _, bad := range []rune{0x1b, 0x07, '\t', '\r', '\n'} {
				if strings.ContainsRune(item.Detail, bad) {
					t.Errorf("control byte %q reached the work record: %q", bad, item.Detail)
				}
			}
		}
	}
	rendered := transcriptText(m)
	if !strings.Contains(rendered, "archived 5 failed 2") {
		t.Errorf("a tab must become a space, not disappear:\n%s", rendered)
	}
}

// AC-1 edge: a failure with no message still says it failed.
func TestFailedNonRenderToolWithNoDetailSaysSo(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "archive_message",
			Data: json.RawMessage(`{"ok":false}`),
		},
	)
	if got := m.activity[0].Detail; got != "failed" {
		t.Errorf("expected the step to say it failed, got %q", got)
	}
}

// AC-2: the #2723 payload class. A truncated partial-success batch summary is
// an ordinary result — never reported as a failure.
func TestTruncatedPartialSuccessBatchProducesNoError(t *testing.T) {
	truncated := `{"succeeded": ["m1", "m2", "m3"], "failed": [{"message_id": "m4", "error": "not fou`
	dataBytes, err := json.Marshal(map[string]any{"summary": truncated, "success": true})
	if err != nil {
		t.Fatalf("fixture setup: %v", err)
	}
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message_batch"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "archive_message_batch",
			Data: json.RawMessage(dataBytes),
		},
	)
	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("a partial-success batch must not be reported as a failure: %+v", msg)
		}
	}
	if item := m.activity[0]; item.Success == nil || !*item.Success {
		t.Errorf("tick must stay green for a partial-success batch, got %v", item.Success)
	}
}

// A silent payload proves nothing either way, so it must not manufacture a
// failure the tool never reported.
func TestSilentNonRenderPayloadProducesNoError(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "some_tool"},
		event.CanonicalToolResultEvent{
			Type: "tool_result", Tool: "some_tool",
			Data: json.RawMessage(`{"latency_ms":12}`),
		},
	)
	if item := m.activity[0]; item.Success == nil || !*item.Success {
		t.Errorf("a silent payload must not tick red, got %v", item.Success)
	}
	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("a silent payload must not produce an error: %+v", msg)
		}
	}
}

// A non-render tool never draws a card, failed or not.
func TestFailedNonRenderToolDrawsNoCard(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "archive_message",
		Data: json.RawMessage(`{"ok":false,"error":"boom"}`),
	})
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			t.Fatalf("a non-render tool must never produce a card: %+v", msg)
		}
	}
}
