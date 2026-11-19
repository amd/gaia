package test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/daemon"
)

// fakeDaemon is an httptest server that answers the daemon's Agent Hub control
// plane, paired with an instance.json in an isolated GAIA_DAEMON_HOME.
type fakeDaemon struct {
	t   *testing.T
	srv *httptest.Server

	mu sync.Mutex
	// catalogBody is served from GET /daemon/v1/catalog.
	catalogBody map[string]any

	// missingRoutes are paths this daemon does not have — a build older than the
	// client talking to it. They answer the way a web framework answers an
	// unrouted path: a bare 404, with no explanation of its own.
	missingRoutes map[string]bool
	// refusals are paths that exist and answer with their own explanation.
	refusals map[string]refusal
}

// refusals are routes that answer with their own explanation — a 404 the route
// ITSELF sends, about the thing being asked for rather than about the daemon.
type refusal struct {
	status int
	detail string
}

// refuseRoute makes a route answer with its own structured refusal.
func (f *fakeDaemon) refuseRoute(path string, status int, detail string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.refusals == nil {
		f.refusals = map[string]refusal{}
	}
	f.refusals[path] = refusal{status: status, detail: detail}
}

// omitRoute makes the fake behave like a daemon predating that route.
func (f *fakeDaemon) omitRoute(path string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.missingRoutes == nil {
		f.missingRoutes = map[string]bool{}
	}
	f.missingRoutes[path] = true
}

func newFakeDaemon(t *testing.T) *fakeDaemon {
	t.Helper()

	dir := t.TempDir()
	t.Setenv(daemon.EnvHome, dir)

	f := &fakeDaemon{t: t}
	f.catalogBody = notesCatalog()
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

	f.mu.Lock()
	absent := f.missingRoutes[r.URL.Path]
	refused, isRefused := f.refusals[r.URL.Path]
	f.mu.Unlock()
	if absent {
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]any{"detail": "Not Found"})
		return
	}
	if isRefused {
		w.WriteHeader(refused.status)
		writeJSON(w, map[string]any{"detail": refused.detail})
		return
	}

	switch {
	case r.URL.Path == daemon.APIPrefix+"/status":
		writeJSON(w, map[string]any{"service": daemon.ServiceID, "pid": os.Getpid()})

	case r.URL.Path == daemon.APIPrefix+"/catalog":
		f.mu.Lock()
		body := f.catalogBody
		f.mu.Unlock()
		writeJSON(w, body)

	case r.URL.Path == daemon.APIPrefix+"/agents":
		writeJSON(w, map[string]any{"agents": []map[string]any{
			{"agent_id": "notes", "state": "stopped"},
		}})

	default:
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]any{"detail": "no route " + r.URL.Path})
	}
}

// notesCatalog is a GET /daemon/v1/catalog body with one supervised,
// non-verified agent.
func notesCatalog() map[string]any {
	entry := map[string]any{
		"id":                  "notes",
		"name":                "Notes",
		"description":         "Note taking and search",
		"category":            "Productivity",
		"icon":                "📝",
		"author":              "AMD",
		"security_tier":       "experimental",
		"permissions":         []string{"filesystem:read", "filesystem:write"},
		"download_size_bytes": 39845888,
		"latest_version":      "0.5.0",
		"deprecated":          false,
		"installed":           false,
		"installed_version":   nil,
		"update_available":    false,
		"supervised":          true,
	}
	return map[string]any{
		"agents":                []map[string]any{entry},
		"offline":               false,
		"source":                "network",
		"generated_at":          "2026-07-24T00:00:00Z",
		"hub_url":               "https://hub.amd-gaia.ai",
		"unsupervised_filtered": []string{"chat", "doc"},
	}
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
