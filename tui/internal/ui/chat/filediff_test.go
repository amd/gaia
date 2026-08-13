package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// ============================================================================
// diffCardData unit tests
// ============================================================================

func TestDiffCardDataBuildsPayloadFromFileToolResult(t *testing.T) {
	data := json.RawMessage(`{"status":"success","file_path":"src/app.py","diff":"@@ -1 +1 @@\n-old\n+new\n"}`)
	payload, ok := diffCardData(data)
	if !ok {
		t.Fatal("expected diffCardData to detect a diff")
	}

	var decoded struct {
		Title   string `json:"title"`
		Unified string `json:"unified"`
	}
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatalf("payload did not decode as the diff card schema: %v", err)
	}
	if decoded.Title != "src/app.py" {
		t.Errorf("title = %q, want the file_path", decoded.Title)
	}
	if decoded.Unified != "@@ -1 +1 @@\n-old\n+new\n" {
		t.Errorf("unified = %q, want the diff verbatim", decoded.Unified)
	}
}

func TestDiffCardDataDefaultsTitleWhenFilePathMissing(t *testing.T) {
	data := json.RawMessage(`{"status":"success","diff":"@@ -1 +1 @@\n-a\n+b\n"}`)
	payload, ok := diffCardData(data)
	if !ok {
		t.Fatal("expected diffCardData to detect a diff")
	}
	if !containsJSONField(t, payload, "title", "file") {
		t.Errorf("expected a non-empty fallback title, got %s", payload)
	}
}

func TestDiffCardDataRejectsErrorResult(t *testing.T) {
	data := json.RawMessage(`{"status":"error","error":"boom","diff":"@@ -1 +1 @@\n-a\n+b\n"}`)
	if _, ok := diffCardData(data); ok {
		t.Error("an error result must never produce a diff card")
	}
}

func TestDiffCardDataRejectsBinaryResult(t *testing.T) {
	data := json.RawMessage(`{"status":"success","is_binary":true,"diff":""}`)
	if _, ok := diffCardData(data); ok {
		t.Error("a binary-file result must never produce a diff card")
	}
}

func TestDiffCardDataRejectsEmptyDiff(t *testing.T) {
	for _, data := range []json.RawMessage{
		json.RawMessage(`{"status":"success","diff":""}`),
		json.RawMessage(`{"status":"success","diff":"   "}`),
		json.RawMessage(`{"status":"success"}`),
	} {
		if _, ok := diffCardData(data); ok {
			t.Errorf("an empty/absent diff must not produce a card: %s", data)
		}
	}
}

func TestDiffCardDataRejectsMalformedOrEmptyPayload(t *testing.T) {
	for _, data := range []json.RawMessage{nil, {}, json.RawMessage(`not json`), json.RawMessage(`[1,2,3]`)} {
		if _, ok := diffCardData(data); ok {
			t.Errorf("malformed/empty data must not produce a card: %s", data)
		}
	}
}

func containsJSONField(t *testing.T, payload json.RawMessage, field, want string) bool {
	t.Helper()
	var m map[string]string
	if err := json.Unmarshal(payload, &m); err != nil {
		t.Fatalf("payload not decodable: %v", err)
	}
	return m[field] == want
}

// ============================================================================
// End-to-end: canonical tool_result -> a diff card in the transcript
// ============================================================================

// The flagship path: EVERY text-file edit the agent performs must render as
// a diff card, whichever of the file-editing tools produced it -- not just
// Python-specific tools, and without the sidecar having to declare
// `tool_result.render` (file_io_tools.py's tools use the ordinary
// status-based envelope every other file tool in the codebase uses).
func TestFileEditToolResultDrawsADiffCard(t *testing.T) {
	for _, tool := range []string{"write_file", "edit_file", "write_python_file", "edit_python_file", "write_markdown_file", "replace_function", "generate_diff"} {
		t.Run(tool, func(t *testing.T) {
			m := feed(t, newTestChat(t),
				event.CanonicalToolCallEvent{Type: "tool_call", Tool: tool},
				event.CanonicalToolResultEvent{
					Type: "tool_result",
					Tool: tool,
					Data: json.RawMessage(`{"status":"success","file_path":"notes.md","diff":"@@ -1,2 +1,2 @@\n context\n-old text\n+new text\n","additions":1,"deletions":1}`),
				},
			)

			var card *Message
			for i := range m.messages {
				if m.messages[i].Role == RoleCard {
					card = &m.messages[i]
				}
			}
			if card == nil {
				t.Fatalf("%s's tool_result carrying a diff produced no card message", tool)
			}
			if card.Render != "diff" {
				t.Errorf("card.Render = %q, want \"diff\"", card.Render)
			}

			rendered := ansi.Strip(m.renderMessage(card, nil))
			t.Logf("\n%s", rendered)
			for _, want := range []string{"notes.md", "old text", "new text"} {
				if !strings.Contains(rendered, want) {
					t.Errorf("%s card render missing %q:\n%s", tool, want, rendered)
				}
			}
		})
	}
}

// A file tool that ran but produced no textual change (no diff key, or an
// empty one -- e.g. read_file, or a write that exactly reproduced existing
// content) must not draw an empty/spurious diff card.
func TestNonDiffToolResultDrawsNoCard(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "read_file"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "read_file",
			Data: json.RawMessage(`{"status":"success","file_path":"notes.md","content":"hello"}`),
		},
	)
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			t.Fatalf("read_file (no diff field) unexpectedly produced a card: %+v", msg)
		}
	}
}

// A failed write/edit (e.g. blocked by a guardrail) must not draw a diff
// card even if a stale "diff" field happens to be present in the payload.
func TestFailedFileToolResultDrawsNoCard(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "write_file"},
		event.CanonicalToolResultEvent{
			Type: "tool_result",
			Tool: "write_file",
			Data: json.RawMessage(`{"status":"error","error":"Access denied: /etc/passwd is not in allowed paths"}`),
		},
	)
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			t.Fatalf("a failed write_file unexpectedly produced a card: %+v", msg)
		}
	}
}

// A binary file is detected and skipped -- no diff card, but the outcome
// (including the size summary) still reaches the activity line via the
// ordinary tool_result summary/preview mechanism.
func TestBinaryFileToolResultDrawsNoCard(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "write_file"},
		event.CanonicalToolResultEvent{
			Type:    "tool_result",
			Tool:    "write_file",
			Data:    json.RawMessage(`{"status":"success","file_path":"logo.png","is_binary":true,"diff":"","size_bytes":204800,"summary":"binary file (200.0 KB) — diff skipped"}`),
			Preview: "binary file (200.0 KB) — diff skipped",
		},
	)
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			t.Fatalf("a binary-file write unexpectedly produced a diff card: %+v", msg)
		}
	}
}

// A tool that explicitly declares its OWN render card (e.g. the email
// agent's pre-scan) must not have that card overridden or duplicated by the
// generic diff detection, even in the hypothetical case its payload also
// carried a "diff"-shaped key.
func TestDeclaredRenderCardIsNotShadowedByDiffDetection(t *testing.T) {
	m := feed(t, newTestChat(t),
		event.CanonicalToolCallEvent{Type: "tool_call", Tool: "pre_scan_inbox"},
		event.CanonicalToolResultEvent{
			Type:   "tool_result",
			Tool:   "pre_scan_inbox",
			Render: "email_pre_scan",
			Data:   json.RawMessage(prescanPayload),
		},
	)
	var cards int
	for _, msg := range m.messages {
		if msg.Role == RoleCard {
			cards++
			if msg.Render != "email_pre_scan" {
				t.Errorf("unexpected extra/altered card: render=%q", msg.Render)
			}
		}
	}
	if cards != 1 {
		t.Errorf("expected exactly 1 card, got %d", cards)
	}
}
