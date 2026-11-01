package chat

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLatestMediaCardRequiresAnExistingAbsoluteFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "result.png")
	if err := os.WriteFile(path, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}

	m := newTestChat(t)
	m.messages = []Message{{
		Role:   RoleCard,
		Render: "media",
		Data:   json.RawMessage(`{"kind":"media","path":` + strconvQuote(path) + `,"media_type":"image"}`),
	}}
	card, ok := m.latestMediaCard()
	if !ok || card.Path != path {
		t.Fatalf("latestMediaCard() = {%+v, %v}, want the existing absolute file", card, ok)
	}

	m.messages[0].Data = json.RawMessage(`{"kind":"media","path":"relative.png","media_type":"image"}`)
	if _, ok := m.latestMediaCard(); ok {
		t.Fatal("latestMediaCard accepted a relative path")
	}
}

func TestMediaActionCmdReportsMissingCard(t *testing.T) {
	msg, ok := newTestChat(t).mediaActionCmd("open")().(mediaActionResultMsg)
	if !ok {
		t.Fatal("mediaActionCmd returned an unexpected message type")
	}
	if msg.err == nil || !strings.Contains(msg.err.Error(), "no generated media card") {
		t.Fatalf("mediaActionCmd error = %v, want missing-card error", msg.err)
	}
	if msg.path != "" {
		t.Fatalf("missing-card result path = %q, want empty", msg.path)
	}
}

func TestMediaActionCmdRefusesRemoteSessions(t *testing.T) {
	t.Setenv("SSH_CONNECTION", "client server 22 22")
	path := filepath.Join(t.TempDir(), "result.png")
	if err := os.WriteFile(path, []byte("png"), 0o600); err != nil {
		t.Fatal(err)
	}

	m := newTestChat(t)
	m.messages = []Message{{
		Role:   RoleCard,
		Render: "media",
		Data:   json.RawMessage(`{"kind":"media","path":` + strconvQuote(path) + `,"media_type":"image"}`),
	}}
	msg, ok := m.mediaActionCmd("open")().(mediaActionResultMsg)
	if !ok {
		t.Fatal("mediaActionCmd returned an unexpected message type")
	}
	if msg.err == nil || !strings.Contains(msg.err.Error(), "unavailable over SSH") {
		t.Fatalf("remote media action error = %v, want SSH refusal", msg.err)
	}
	if msg.path != path {
		t.Fatalf("remote media action path = %q, want %q", msg.path, path)
	}
}

func TestMediaStatusDoesNotPrintAnEmptyPath(t *testing.T) {
	got := mediaStatus("", "open", errors.New("launcher failed"))
	if got != "could not open: launcher failed" {
		t.Fatalf("mediaStatus() = %q, want no empty path", got)
	}
}

// Keep JSON path quoting in one place so Windows paths remain valid JSON in tests.
func strconvQuote(path string) string {
	encoded, err := json.Marshal(path)
	if err != nil {
		panic(err)
	}
	return string(encoded)
}
