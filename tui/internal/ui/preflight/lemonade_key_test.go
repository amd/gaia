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
	"sync/atomic"
	"testing"
)

// GAIA's embedded Lemonade generates an API key and writes it to its state
// file. An unauthenticated probe gets 401 from it, which this screen reported
// as "Lemonade not running" — while the server was healthy and serving — and
// then offered a second install that would fight for the same port.
func TestLemonadeAPIKey(t *testing.T) {
	t.Setenv("GAIA_HOME", "")
	t.Setenv("LEMONADE_BASE_URL", "")
	writeState := func(t *testing.T, key string) {
		t.Helper()
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		dir := filepath.Join(home, ".gaia", "lemonade")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		body, _ := json.Marshal(map[string]any{"pid": 1, "port": 63207, "api_key": key})
		if err := os.WriteFile(filepath.Join(dir, "state.json"), body, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	t.Run("reads the embedded server's key", func(t *testing.T) {
		t.Setenv("LEMONADE_API_KEY", "")
		writeState(t, "from-state-file")
		if got := lemonadeAPIKey(); got != "from-state-file" {
			t.Fatalf("got %q, want the key from state.json", got)
		}
	})

	t.Run("an explicit env var wins", func(t *testing.T) {
		writeState(t, "from-state-file")
		t.Setenv("LEMONADE_API_KEY", "from-env")
		if got := lemonadeAPIKey(); got != "from-env" {
			t.Fatalf("got %q, want the explicitly configured credential", got)
		}
	})

	t.Run("no state file and no env is not an error", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LEMONADE_API_KEY", "")
		if got := lemonadeAPIKey(); got != "" {
			t.Fatalf("got %q, want empty for an unauthenticated server", got)
		}
	})

	t.Run("a malformed state file is not fatal", func(t *testing.T) {
		home := t.TempDir()
		t.Setenv("HOME", home)
		t.Setenv("USERPROFILE", home)
		t.Setenv("LEMONADE_API_KEY", "")
		dir := filepath.Join(home, ".gaia", "lemonade")
		_ = os.MkdirAll(dir, 0o755)
		_ = os.WriteFile(filepath.Join(dir, "state.json"), []byte("{not json"), 0o600)
		if got := lemonadeAPIKey(); got != "" {
			t.Fatalf("got %q, want empty rather than a panic", got)
		}
	})
}

// The embedded server picks its port at start time, so the fixed 13305/8000
// list could never reach it -- this screen called GAIA's own model server
// "not running" and offered to install a second one.
func TestReadEmbeddedLemonade(t *testing.T) {
	t.Setenv("GAIA_HOME", "")
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	dir := filepath.Join(home, ".gaia", "lemonade")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]any{"pid": 9, "port": 63207, "api_key": "k"})
	if err := os.WriteFile(filepath.Join(dir, "state.json"), body, 0o600); err != nil {
		t.Fatal(err)
	}

	state := readEmbeddedLemonade()
	if state == nil {
		t.Fatal("got nil, want the embedded server's recorded state")
	}
	if state.Port != 63207 {
		t.Fatalf("port %d, want the dynamically chosen 63207", state.Port)
	}
	if state.APIKey != "k" {
		t.Fatalf("key %q, want k", state.APIKey)
	}
}

func TestReadEmbeddedLemonadeAbsent(t *testing.T) {
	t.Setenv("GAIA_HOME", "")
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	if state := readEmbeddedLemonade(); state != nil {
		t.Fatalf("got %+v, want nil when no embedded server was ever started", state)
	}
}

func writeProbeEmbeddedState(t *testing.T, serverURL string) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("GAIA_HOME", dir)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	u, err := url.Parse(serverURL)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatal(err)
	}
	dir = filepath.Join(dir, "lemonade")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(map[string]any{"port": port, "api_key": "embedded-probe-key"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "state.json"), body, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestPreflightDoesNotReplaceFailingEmbeddedEndpointWithAnotherServer(t *testing.T) {
	private := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("Authorization") != "Bearer embedded-probe-key" {
			t.Error("private probe did not receive its credential")
		}
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer private.Close()
	var fallbackCalls atomic.Int32
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		fallbackCalls.Add(1)
		if req.Header.Get("Authorization") != "" {
			t.Error("fallback port received another server's credential")
		}
		fmt.Fprint(w, `{"data":[]}`)
	}))
	defer fallback.Close()
	writeProbeEmbeddedState(t, private.URL)
	u, _ := url.Parse(fallback.URL)
	oldPorts := lemonadePorts
	lemonadePorts = []string{u.Port()}
	t.Cleanup(func() { lemonadePorts = oldPorts })
	base, reachable, _ := probeLemonadeHTTP(context.Background())
	privateURL, _ := url.Parse(private.URL)
	if reachable || base != "http://localhost:"+privateURL.Port()+"/api/v1" || fallbackCalls.Load() != 0 {
		t.Fatalf("unrelated server masked a failing embedded connection: reachable=%v base=%s fallbackCalls=%d", reachable, base, fallbackCalls.Load())
	}
	// Once explicitly chosen, the other endpoint is usable, but it must not
	// inherit the private instance's automatically discovered credential.
	t.Setenv("LEMONADE_BASE_URL", fallback.URL+"/api/v1")
	_, reachable, _ = probeLemonadeHTTP(context.Background())
	if !reachable || fallbackCalls.Load() != 1 {
		t.Fatal("explicit endpoint did not override embedded discovery")
	}
}

func TestStaleEmbeddedEndpointDoesNotPassAgainstHealthyDefaultPort(t *testing.T) {
	stale := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {}))
	writeProbeEmbeddedState(t, stale.URL)
	fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		t.Error("stale embedded state must not trigger a different server probe")
		fmt.Fprint(w, `{"data":[]}`)
	}))
	defer fallback.Close()
	stale.Close()
	u, _ := url.Parse(fallback.URL)
	oldPorts := lemonadePorts
	lemonadePorts = []string{u.Port()}
	t.Cleanup(func() { lemonadePorts = oldPorts })
	if _, reachable, _ := probeLemonadeHTTP(context.Background()); reachable {
		t.Fatal("stale embedded endpoint was incorrectly reported ready")
	}
}

func TestPreflightDoesNotFollowEmbeddedServerRedirect(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		t.Error("redirect target must never receive a probe or credential")
	}))
	defer target.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("Authorization") != "Bearer embedded-probe-key" {
			t.Error("source probe did not receive its credential")
		}
		http.Redirect(w, req, target.URL, http.StatusTemporaryRedirect)
	}))
	defer source.Close()
	writeProbeEmbeddedState(t, source.URL)
	t.Setenv("LEMONADE_BASE_URL", source.URL+"/api/v1")
	_, reachable, _ := probeLemonadeHTTP(context.Background())
	if reachable {
		t.Fatal("redirected server was reported ready")
	}
}

func TestPreflightNormalizesExplicitBareLemonadeOrigin(t *testing.T) {
	t.Setenv("GAIA_HOME", t.TempDir())
	t.Setenv("LEMONADE_API_KEY", "")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path != "/api/v1/models" {
			t.Errorf("probe used %q instead of Lemonade's API path", req.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		fmt.Fprint(w, `{"data":[]}`)
	}))
	defer server.Close()
	t.Setenv("LEMONADE_BASE_URL", server.URL)
	base, ready, _ := probeLemonadeHTTP(context.Background())
	if !ready || base != server.URL+"/api/v1" {
		t.Fatal("bare Lemonade origin was not normalized consistently")
	}
}
