package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// AC-1: a failed tool that declares no `render` key must put its own error
// text in the transcript. Every email tool except pre_scan_inbox is in this
// class, so before this the whole failure surface was a tick that scrolls away.
func TestFailedNonRenderToolSurfacesItsErrorText(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "send_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "send_message",
			Data: json.RawMessage(`{"ok":false,"error":"CONNECTOR_ERROR: google is not connected.\nRun: gaia connectors connect google"}`),
		},
	)

	msg := lastToolErrorMessage(t, m)
	if !strings.Contains(msg.Content, "CONNECTOR_ERROR") ||
		!strings.Contains(msg.Content, "gaia connectors connect google") {
		t.Errorf("the tool's own remedy was lost: %q", msg.Content)
	}
	if !strings.Contains(msg.Content, "send_message") {
		t.Errorf("the failing tool must be named: %q", msg.Content)
	}
	// AC-1 says verbatim "including line breaks" — the remedy is on its own
	// line in the tool's text and must stay there.
	if !strings.Contains(msg.Content, "\nRun: gaia connectors connect google") {
		t.Errorf("the message's line break was flattened: %q", msg.Content)
	}
}

// AC-1, rendered: the text must actually reach the viewport, not just the
// message slice. Asserted on a short unwrappable token for the 80-column
// harness (the cards_test.go idiom).
func TestFailedNonRenderToolIsVisibleInTheTranscript(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "archive_message",
			Data: json.RawMessage(`{"status":"error","error":"boom"}`),
		},
	)
	m.updateViewport()
	rendered := ansi.Strip(m.viewport.View())
	if !strings.Contains(rendered, "boom") {
		t.Errorf("a failed non-render tool printed nothing the user can read:\n%s", rendered)
	}
}

// AC-4: a mid-turn failure the agent recovers from must not read as a failed
// turn. The inline treatment is the explicit decision — a one-line aside, not
// the bordered error panel the render path draws — so the answer that follows
// stays the loudest thing on screen.
func TestRecoveredMidTurnFailureStaysInline(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "search_messages"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "search_messages",
			Data: json.RawMessage(`{"ok":false,"error":"rate limited, retrying"}`),
		},
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "search_messages"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "search_messages",
			Data: json.RawMessage(`{"ok":true,"count":3}`),
		},
		event.CanonicalFinalEvent{Type: "final", Answer: "You have 3 matching emails."},
	)

	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("a recovered mid-turn failure must not draw the full error panel: %+v", msg)
		}
	}
	if _, ok := toolErrorMessage(m); !ok {
		t.Fatal("the failed attempt still has to be visible somewhere")
	}
	if spacedAfter(RoleToolError) {
		t.Error("an inline aside must stay tight against the work around it")
	}

	m.updateViewport()
	rendered := ansi.Strip(m.viewport.View())
	if !strings.Contains(rendered, "rate limited") {
		t.Errorf("the failed attempt vanished:\n%s", rendered)
	}
	if !strings.Contains(rendered, "You have 3 matching emails.") {
		t.Errorf("the recovered answer must still be there:\n%s", rendered)
	}
}

// AC-1 companion: the activity tick must agree with the transcript. A tool
// whose payload says it failed cannot keep a green ✓.
func TestFailedNonRenderToolTicksFailed(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "archive_message"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "archive_message",
			Data: json.RawMessage(`{"ok":false,"error":"mailbox is read-only"}`),
		},
	)
	if len(m.activity) != 1 {
		t.Fatalf("expected one tool activity line, got %+v", m.activity)
	}
	if item := m.activity[0]; item.Success == nil || *item.Success {
		t.Errorf("a failed non-render tool must tick red, got %v", item.Success)
	}
}

// AC-3: the same newline-preserving control-character strip guards this sink.
// The RoleToolError renderer has no scrubbing of its own.
func TestFailedNonRenderErrorSanitizesControlBytes(t *testing.T) {
	malicious := "archived 5\tfailed 2\r\nline three\n\x1b[31mred\x1b[0m line four\x07 line five"
	encoded, err := json.Marshal(malicious)
	if err != nil {
		t.Fatal(err)
	}
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "archive_message",
		Data: json.RawMessage(`{"ok":false,"error":` + string(encoded) + `}`),
	})

	msg := lastToolErrorMessage(t, m)
	for _, bad := range []rune{0x1b, 0x07, '\t', '\r'} {
		if strings.ContainsRune(msg.Content, bad) {
			t.Errorf("control byte %q reached Message.Content: %q", bad, msg.Content)
		}
	}
	if !strings.Contains(msg.Content, "archived 5 failed 2") {
		t.Errorf("a tab must become a space, not disappear: %q", msg.Content)
	}
	if !strings.Contains(msg.Content, "failed 2\nline three") {
		t.Errorf("\\r\\n must collapse to a single \\n: %q", msg.Content)
	}
}

// AC-1 edge: a failure with no message must not render an empty aside.
func TestFailedNonRenderToolWithNoDetailSaysSo(t *testing.T) {
	m := feed(t, newTestChat(t), event.CanonicalToolResultEvent{
		Type: "tool_result", Tool: "archive_message",
		Data: json.RawMessage(`{"ok":false}`),
	})
	msg := lastToolErrorMessage(t, m)
	if !strings.Contains(msg.Content, "no detail") {
		t.Errorf("expected the aside to say the tool reported no detail, got %q", msg.Content)
	}
}

// AC-2: the #2723 payload class. A truncated partial-success batch summary is
// an ordinary result — it must stay free of any error message on this surface
// now that the render gate no longer shields it.
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
		if msg.Role == RoleError || msg.Role == RoleToolError {
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
	for _, msg := range m.messages {
		if msg.Role == RoleToolError || msg.Role == RoleError {
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

func toolErrorMessage(m ChatModel) (Message, bool) {
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleToolError {
			return m.messages[i], true
		}
	}
	return Message{}, false
}

func lastToolErrorMessage(t *testing.T, m ChatModel) Message {
	t.Helper()
	msg, ok := toolErrorMessage(m)
	if !ok {
		t.Fatal("no RoleToolError message was produced for the failed tool")
	}
	if strings.TrimSpace(msg.Content) == "" {
		t.Fatal("an empty aside must never be rendered")
	}
	return msg
}
