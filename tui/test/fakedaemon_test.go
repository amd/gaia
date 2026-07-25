package test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
)

// fakeDaemon is an httptest server that answers the daemon's Agent Hub control
// plane, paired with an instance.json in an isolated GAIA_DAEMON_HOME.
//
// It records every install request body, which is what lets the trust-gate test
// assert the thing that matters: that nothing retried with trusted=true on the
// user's behalf.
type fakeDaemon struct {
	t   *testing.T
	srv *httptest.Server

	mu sync.Mutex
	// installBodies is one entry per POST .../install, in order.
	installBodies []map[string]any
	// uninstalled is one entry per DELETE, in order.
	uninstalled []string
	// catalogBody is served from GET /daemon/v1/catalog.
	catalogBody map[string]any
	// progress is the queue of install-status bodies; the last one repeats.
	progress []map[string]any
	// installedAfterTrust flips the catalog entry to installed once a trusted
	// install has been accepted.
	trustedAccepted bool
}

func newFakeDaemon(t *testing.T) *fakeDaemon {
	t.Helper()

	dir := t.TempDir()
	t.Setenv(daemon.EnvHome, dir)

	f := &fakeDaemon{t: t}
	f.catalogBody = emailCatalog(false)
	f.progress = []map[string]any{
		{"agent_id": "email", "status": "running", "phase": "download", "percent": 42.0, "version": "0.5.0"},
		{"agent_id": "email", "status": "completed", "phase": "completed", "percent": 100.0, "version": "0.5.0"},
	}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)

	inst := daemon.Instance{
		PID:        os.Getpid(),
		Port:       f.port(),
		Token:      "test-token",
		Host:       daemon.DefaultHost,
		APIVersion: "1.1",
		Service:    daemon.ServiceID,
	}
	raw, err := json.Marshal(inst)
	if err != nil {
		t.Fatalf("marshal instance: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "instance.json"), raw, 0o600); err != nil {
		t.Fatalf("write instance.json: %v", err)
	}
	return f
}

// client builds a hub client wired to this fake. PIDAlive is stubbed so the
// two-check trust rule passes against the test process.
func (f *fakeDaemon) client() *catalog.HubClient {
	return catalog.NewHubClientWith(daemon.New(daemon.Options{
		PIDAlive: func(int) bool { return true },
	}))
}

func (f *fakeDaemon) port() int {
	u, err := url.Parse(f.srv.URL)
	if err != nil {
		f.t.Fatalf("parse server URL: %v", err)
	}
	p, err := strconv.Atoi(u.Port())
	if err != nil {
		f.t.Fatalf("parse server port: %v", err)
	}
	return p
}

func (f *fakeDaemon) handle(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != daemon.AuthScheme+" test-token" {
		w.WriteHeader(http.StatusUnauthorized)
		writeJSON(w, map[string]any{"detail": "invalid client token"})
		return
	}

	switch {
	case r.URL.Path == daemon.APIPrefix+"/status":
		writeJSON(w, map[string]any{"service": daemon.ServiceID, "pid": os.Getpid()})

	case r.URL.Path == daemon.APIPrefix+"/catalog":
		f.mu.Lock()
		body := f.catalogBody
		if f.trustedAccepted {
			body = emailCatalog(true)
		}
		f.mu.Unlock()
		writeJSON(w, body)

	case r.URL.Path == daemon.APIPrefix+"/agents":
		writeJSON(w, map[string]any{"agents": []map[string]any{
			{"agent_id": "email", "state": "stopped"},
		}})

	case strings.HasSuffix(r.URL.Path, "/install"):
		f.handleInstall(w, r)

	case strings.HasSuffix(r.URL.Path, "/install-status"):
		f.mu.Lock()
		body := f.progress[0]
		if len(f.progress) > 1 {
			f.progress = f.progress[1:]
		}
		f.mu.Unlock()
		writeJSON(w, body)

	case r.Method == http.MethodDelete:
		id := strings.TrimPrefix(r.URL.Path, daemon.APIPrefix+"/agents/")
		f.mu.Lock()
		f.uninstalled = append(f.uninstalled, id)
		f.mu.Unlock()
		writeJSON(w, map[string]any{"agent_id": id, "status": "uninstalled"})

	default:
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]any{"detail": "no route " + r.URL.Path})
	}
}

func (f *fakeDaemon) handleInstall(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)

	f.mu.Lock()
	f.installBodies = append(f.installBodies, body)
	trusted := body["trusted"] == true
	if trusted {
		f.trustedAccepted = true
	}
	f.mu.Unlock()

	// Mirrors the real daemon: `email` is in the experimental tier, so an
	// install without an explicit opt-in is a 403 naming --trust.
	if !trusted {
		w.WriteHeader(http.StatusForbidden)
		writeJSON(w, map[string]any{"detail": "installing 'email' runs code AMD has not verified. " +
			"From the CLI: `gaia hub install email --trust`."})
		return
	}
	w.WriteHeader(http.StatusAccepted)
	writeJSON(w, map[string]any{"agent_id": "email", "status": "queued", "version": "0.5.0"})
}

func (f *fakeDaemon) installCallCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.installBodies)
}

func (f *fakeDaemon) installBody(i int) map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	if i >= len(f.installBodies) {
		f.t.Fatalf("no install call #%d (saw %d)", i, len(f.installBodies))
	}
	return f.installBodies[i]
}

func (f *fakeDaemon) uninstallCalls() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.uninstalled...)
}

// emailCatalog is a GET /daemon/v1/catalog body with one supervised,
// non-verified agent — the real shape as of the install routes landing.
func emailCatalog(installed bool) map[string]any {
	entry := map[string]any{
		"id":                  "email",
		"name":                "Email",
		"description":         "Email triage, drafting, and calendar",
		"category":            "Productivity",
		"icon":                "📧",
		"author":              "AMD",
		"security_tier":       "experimental",
		"permissions":         []string{"gmail:read", "gmail:send", "calendar:write"},
		"download_size_bytes": 39845888,
		"latest_version":      "0.5.0",
		"deprecated":          false,
		"installed":           installed,
		"installed_version":   nil,
		"update_available":    false,
		"supervised":          true,
	}
	if installed {
		entry["installed_version"] = "0.5.0"
	}
	return map[string]any{
		"agents":                []map[string]any{entry},
		"offline":               false,
		"source":                "network",
		"generated_at":          "2026-07-24T00:00:00Z",
		"hub_url":               "https://hub.amd-gaia.ai",
		"unsupervised_filtered": []string{"code", "docker"},
	}
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
