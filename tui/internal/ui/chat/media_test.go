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

	unknown := filepath.Join(t.TempDir(), "result.txt")
	if err := os.WriteFile(unknown, []byte("not media"), 0o600); err != nil {
		t.Fatal(err)
	}
	m.messages[0].Data = json.RawMessage(`{"kind":"media","path":` + strconvQuote(unknown) + `,"media_type":"image"}`)
	if _, ok := m.latestMediaCard(); ok {
		t.Fatal("latestMediaCard accepted an unknown extension")
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

func TestDeletedMediaFileIsRefusedWithoutACrash(t *testing.T) {
	path := filepath.Join(t.TempDir(), "gone.wav")
	if err := os.WriteFile(path, []byte("wav"), 0o600); err != nil {
		t.Fatal(err)
	}
	m := newTestChat(t)
	m.messages = []Message{{
		Role:   RoleCard,
		Render: "media",
		Data:   json.RawMessage(`{"kind":"media","path":` + strconvQuote(path) + `,"media_type":"audio"}`),
	}}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if _, ok := m.latestMediaCard(); ok {
		t.Fatal("latestMediaCard accepted a deleted file")
	}
	for _, action := range []string{"open", "reveal"} {
		msg, ok := m.mediaActionCmd(action)().(mediaActionResultMsg)
		if !ok || msg.err == nil {
			t.Fatalf("%s on a deleted file = %+v, want a reported error", action, msg)
		}
	}
	_ = m.statusHints()
}

func TestRemoteAndDevicePathsAreRefused(t *testing.T) {
	for _, p := range []string{
		`\\host\share\a.png`,
		`//host/share/a.png`,
		`\\?\UNC\host\share\a.png`,
		`\\.\C:\a.png`,
		`\??\C:\a.png`,
		"C:\\a\x00.png",
	} {
		if !isRemoteOrDevicePath(p) {
			t.Errorf("isRemoteOrDevicePath(%q) = false, want true", p)
		}
	}
	for _, p := range []string{`C:\Users\me\a.png`, "/home/me/a.png", "/tmp/a b.png"} {
		if isRemoteOrDevicePath(p) {
			t.Errorf("isRemoteOrDevicePath(%q) = true, want false", p)
		}
	}
}

func TestMediaArgvNeverUsesAShell(t *testing.T) {
	path := `/tmp/a b; rm -rf ~ $(x) & "q".png`
	shells := map[string]bool{"sh": true, "bash": true, "cmd": true, "cmd.exe": true, "powershell": true, "pwsh": true}
	for _, goos := range []string{"windows", "darwin", "linux"} {
		for _, action := range []string{"open", "reveal"} {
			argv := mediaArgv(goos, action, path)
			if shells[argv[0]] {
				t.Fatalf("%s/%s runs a shell: %q", goos, action, argv)
			}
			last := argv[len(argv)-1]
			want := path
			switch {
			case action == "reveal" && goos == "windows":
				want = "/select," + path
			case action == "reveal" && goos == "linux":
				want = filepath.Dir(path)
			}
			if last != want {
				t.Fatalf("%s/%s argv = %q, want the path as one final argument %q", goos, action, argv, want)
			}
		}
	}
}
