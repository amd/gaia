package gateway

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// fakeLemonade answers cloud/auth with a fixed discovery count and records
// every request, so a test can see whether the token was dropped again.
type fakeLemonade struct {
	mu         sync.Mutex
	discovered int
	seen       []string
}

func (f *fakeLemonade) start(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.seen = append(f.seen, r.Method+" "+r.URL.Path)
		f.mu.Unlock()
		switch r.URL.Path {
		case "/api/v1/cloud/auth":
			writeJSON(w, map[string]any{
				"models_discovered": f.discovered,
				"auth_state":        map[string]any{"runtime_key_set": true},
			})
		case "/api/v1/models":
			writeJSON(w, map[string]any{"data": []any{}})
		default:
			writeJSON(w, map[string]any{})
		}
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/api/v1"
}

func (f *fakeLemonade) saw(req string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, s := range f.seen {
		if s == req {
			return true
		}
	}
	return false
}

// submitToken types a token on the token stage, presses enter, and feeds the
// result back in. It returns the screen and the command that followed.
func submitToken(t *testing.T, base, token string) (GatewayModel, tea.Cmd) {
	t.Helper()
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(t.TempDir(), "gateway.json"))
	m := New(NewClientAt(base), nil)
	m.haveInit = true
	m.stage = stageToken
	m.tokenInput.Focus()
	m.tokenInput.SetValue(token)

	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd == nil {
		t.Fatal("enter on the token stage sent nothing")
	}
	next, cmd = next.(GatewayModel).Update(cmd())
	return next.(GatewayModel), cmd
}

// runAll executes a command and every command a batch fans out into.
func runAll(cmd tea.Cmd) {
	if cmd == nil {
		return
	}
	if batch, ok := cmd().(tea.BatchMsg); ok {
		for _, c := range batch {
			runAll(c)
		}
	}
}

// A mistyped token found no models, yet it stayed in Lemonade and was saved, so
// cloud chat failed for the session and every later one.
func TestATokenThatFindsNoModelsIsDroppedAndNotSaved(t *testing.T) {
	d := newFakeDaemon(t)
	lem := &fakeLemonade{discovered: 0}
	g, cmd := submitToken(t, lem.start(t), "typo")
	runAll(cmd)

	if !lem.saw("DELETE /api/v1/cloud/auth/" + Provider) {
		t.Fatalf("the token that found nothing was left in Lemonade: %v", lem.seen)
	}
	if want := "POST " + gatewayAuthPath; d.seenPath != want {
		t.Fatalf("daemon got %q, want only the restore of the saved token (%q)", d.seenPath, want)
	}
	if g.pendingPersist != nil {
		t.Fatal("the rejected token is still queued to be saved")
	}
	if g.stage != stageToken {
		t.Fatalf("stage = %d, want the token stage so the user can retry", g.stage)
	}
	if !strings.Contains(g.errMsg, "not saved") || !strings.Contains(g.errMsg, "back in use") {
		t.Fatalf("errMsg = %q, want it to say the token was not saved and the old one is back", g.errMsg)
	}
	if strings.Contains(g.notice, "accepted") {
		t.Fatalf("a token that found nothing must not read as accepted: %q", g.notice)
	}
}

func TestATokenThatFindsModelsIsKeptAndSaved(t *testing.T) {
	d := newFakeDaemon(t)
	lem := &fakeLemonade{discovered: 65}
	g, cmd := submitToken(t, lem.start(t), "good")
	runAll(cmd)

	if lem.saw("DELETE /api/v1/cloud/auth/" + Provider) {
		t.Fatalf("a working token was dropped: %v", lem.seen)
	}
	if want := "POST " + gatewayTokenPath; d.seenPath != want {
		t.Fatalf("daemon got %q, want the token saved (%q)", d.seenPath, want)
	}
	if g.stage != stageModels || g.errMsg != "" {
		t.Fatalf("stage = %d, errMsg = %q; want the model list and no error", g.stage, g.errMsg)
	}
}
