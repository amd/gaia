package preflight

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// fakeHost is a daemon-shaped HTTP server plus the instance.json that points at
// it. Unlike fakeTransport, this drives the REAL daemon client — discovery,
// the two-check trust rule, the version gate, and the 401 replay — so it is the
// only thing in this package that can prove the adapter uses them correctly.
type fakeHost struct {
	t   *testing.T
	srv *httptest.Server
	dir string

	mu       sync.Mutex
	token    string
	pid      int
	rotateOn string // path that triggers a restart-mid-check, once
	rotated  bool
	unauthed int
	seen     []string
}

func newFakeHost(t *testing.T) *fakeHost {
	t.Helper()
	dir := t.TempDir()
	t.Setenv(daemon.EnvHome, dir)

	h := &fakeHost{t: t, dir: dir, token: "token-A", pid: 4242}
	h.srv = httptest.NewServer(http.HandlerFunc(h.handle))
	t.Cleanup(h.srv.Close)
	h.writeInstance()
	return h
}

func (h *fakeHost) port() int {
	u, err := url.Parse(h.srv.URL)
	if err != nil {
		h.t.Fatalf("parse server URL: %v", err)
	}
	p, err := strconv.Atoi(u.Port())
	if err != nil {
		h.t.Fatalf("parse port: %v", err)
	}
	return p
}

// writeInstance records the CURRENT token and pid, exactly as a daemon does on
// every start — which is what makes a restart recoverable at all.
func (h *fakeHost) writeInstance() {
	h.mu.Lock()
	inst := map[string]any{
		"pid": h.pid, "port": h.port(), "token": h.token,
		"host": "127.0.0.1", "api_version": "1.1", "service": "gaia-daemon",
		"started_at": 1750000000.0,
	}
	h.mu.Unlock()

	raw, err := json.Marshal(inst)
	if err != nil {
		h.t.Fatalf("marshal instance: %v", err)
	}
	if err := os.WriteFile(filepath.Join(h.dir, "instance.json"), raw, 0o600); err != nil {
		h.t.Fatalf("write instance.json: %v", err)
	}
}

// restart simulates what a `gaia daemon restart` does to a client holding a
// live token: a new pid, a new token, and a rewritten registry file.
func (h *fakeHost) restart(token string, pid int) {
	h.mu.Lock()
	h.token, h.pid = token, pid
	h.mu.Unlock()
	h.writeInstance()
}

func (h *fakeHost) handle(w http.ResponseWriter, r *http.Request) {
	h.mu.Lock()
	h.seen = append(h.seen, r.Method+" "+r.URL.Path)
	trigger := h.rotateOn != "" && !h.rotated && r.URL.Path == h.rotateOn
	if trigger {
		h.rotated = true
	}
	h.mu.Unlock()

	// The daemon went down and came back up between two calls of one check.
	if trigger {
		h.restart("token-B", 5353)
	}

	h.mu.Lock()
	token, pid := h.token, h.pid
	h.mu.Unlock()

	if r.Header.Get("Authorization") != "Bearer "+token {
		h.mu.Lock()
		h.unauthed++
		h.mu.Unlock()
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"invalid or expired client token"}`))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if r.Method == http.MethodPost && r.URL.Path == "/v1/email/init" {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = w.Write([]byte("✓ already downloaded.\n✓ Provisioning complete.\n"))
		return
	}

	switch r.URL.Path {
	case "/daemon/v1/status":
		fmt.Fprintf(w, `{"service":"gaia-daemon","pid":%d}`, pid)
	case "/daemon/v1/agents":
		_, _ = w.Write([]byte(agentsRunning))
	case "/v1/email/init":
		_, _ = w.Write([]byte(initReady))
	case "/v1/email/connectors":
		_, _ = w.Write([]byte(connectorsReady))
	default:
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"no such route"}`))
	}
}

func (h *fakeHost) unauthorizedCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.unauthed
}

// transport builds the real adapter over the real client, with liveness faked
// so the test can move the pid without spawning processes.
func (h *fakeHost) transport() Transport {
	return NewDaemonTransport(daemon.New(daemon.Options{
		PIDAlive: func(int) bool { return true },
		Logf:     func(format string, args ...any) { h.t.Logf(format, args...) },
	}))
}

// The daemon token rotates on EVERY daemon restart, and a restart also changes
// the pid the registry records. This is the failure mode most likely to bite in
// the real world — a sibling process restarts the daemon while the gate is
// open — and it must recover silently rather than surface a 401.
func TestTokenRotationMidCheckRecovers(t *testing.T) {
	h := newFakeHost(t)
	// The restart lands between the daemon probe and the agents listing, so the
	// gate is holding a token that has already been invalidated.
	h.rotateOn = "/daemon/v1/agents"

	tr := h.transport()
	rep := Check(context.Background(), tr, EmailConfig())

	if !rep.Ready() {
		t.Fatalf("a mid-check daemon restart broke the report:\n%s", rep)
	}
	if got := h.unauthorizedCount(); got != 1 {
		// Exactly one: the call that raced the restart. More means the refreshed
		// instance was thrown away and every later call re-discovered it.
		t.Errorf("saw %d unauthorized calls, want exactly 1", got)
	}

	// A second pass must report the NEW pid — the gate re-reads the registry
	// rather than trusting what it cached before the restart.
	rep = Check(context.Background(), tr, EmailConfig())
	if !rep.Ready() {
		t.Fatalf("the report after the restart is not ready:\n%s", rep)
	}
	row, _ := rep.Find(KeyDaemon)
	if !strings.Contains(row.Line, "5353") {
		t.Errorf("daemon row still reports the pre-restart pid: %q", row.Line)
	}
	if h.unauthorizedCount() != 1 {
		t.Errorf("the second pass hit another 401: the rotated token was not kept")
	}
}

// The same rotation during the RELAYED call, which is the path a mid-run chat
// turn takes too.
func TestTokenRotationOnARelayedCallRecovers(t *testing.T) {
	h := newFakeHost(t)
	h.rotateOn = "/v1/email/init"

	rep := Check(context.Background(), h.transport(), EmailConfig())
	if !rep.Ready() {
		t.Fatalf("a rotation on the relayed readiness call broke the report:\n%s", rep)
	}
	if got := h.unauthorizedCount(); got != 1 {
		t.Errorf("saw %d unauthorized calls, want exactly 1", got)
	}
}

// A daemon that goes away and does NOT come back must fail loudly with the
// daemon's own remedy — never as a raw 401, and never blamed on Lemonade.
func TestADaemonThatDiesMidCheckFailsLoudly(t *testing.T) {
	h := newFakeHost(t)
	tr := h.transport()

	if rep := Check(context.Background(), tr, EmailConfig()); !rep.Ready() {
		t.Fatalf("baseline is not ready:\n%s", rep)
	}
	h.srv.Close() // the daemon is gone; instance.json still points at its port

	rep := Check(context.Background(), tr, EmailConfig())
	row, _ := rep.Find(KeyDaemon)
	if row.State != StateFailed {
		t.Fatalf("a dead daemon left the daemon row at %s:\n%s", row.State.Word(), rep)
	}
	assertRealCommand(t, row)
	if strings.Contains(row.Remedy.Command, "lemonade") {
		t.Errorf("a dead daemon was blamed on Lemonade: %q", row.Remedy.Command)
	}
	if strings.Contains(row.Detail, "401") || strings.Contains(row.Line, "401") {
		t.Errorf("a raw status code reached the user: %q / %q", row.Line, row.Detail)
	}
}

// relocate is a daemon restart that lands on a DIFFERENT port — the case a
// cached instance survives but must not be trusted through.
func (h *fakeHost) relocate(token string, pid int) {
	h.srv.Close()
	h.srv = httptest.NewServer(http.HandlerFunc(h.handle))
	h.t.Cleanup(h.srv.Close)
	h.mu.Lock()
	h.token, h.pid = token, pid
	h.mu.Unlock()
	h.writeInstance()
}

// A failed call must not poison every later one. The gate caches the verified
// instance between calls (the daemon client is stateless by design), so a
// daemon that moved to a new port has to invalidate that cache on failure —
// otherwise every subsequent call keeps dialling a port nothing is listening on
// and the user is stuck until they restart the app.
//
// Provision is the path that shows it: unlike Check it does not re-attach
// first, so it is the one that would keep using a dead record.
func TestATransportFailureIsNotSticky(t *testing.T) {
	h := newFakeHost(t)
	tr := h.transport()

	if rep := Check(context.Background(), tr, EmailConfig()); !rep.Ready() {
		t.Fatalf("baseline is not ready:\n%s", rep)
	}

	h.relocate("token-C", 6464)

	// Nothing can know the daemon moved until a call fails, so the first attempt
	// after the move is expected to fail — loudly, with a real remedy.
	first := Provision(context.Background(), tr, EmailConfig(), nil)
	if first.OK {
		t.Skip("the moved daemon answered on the first try; nothing to prove here")
	}
	if first.Diagnosis.Command == "" {
		t.Errorf("the failure after a move has no remedy: %+v", first.Diagnosis)
	}

	// The second attempt must recover: the stale record is gone and the client
	// re-reads the registry the restarted daemon rewrote.
	second := Provision(context.Background(), tr, EmailConfig(), nil)
	if !second.OK {
		t.Fatalf("the gate never recovered from a daemon that moved: final=%q diag=%s",
			second.Final, second.Diagnosis)
	}
}
