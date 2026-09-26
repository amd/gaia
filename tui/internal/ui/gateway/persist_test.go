package gateway

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// fakeDaemon answers the discovery probe and records what the gateway routes
// were actually sent — asserting the request shape, not just that a call
// happened.
type fakeDaemon struct {
	t    *testing.T
	srv  *httptest.Server
	dir  string
	tok  string
	code int

	seenPath string
	seenAuth string
	seenBody string
}

func newFakeDaemon(t *testing.T) *fakeDaemon {
	t.Helper()
	f := &fakeDaemon{t: t, dir: t.TempDir(), tok: "daemon-client-token", code: http.StatusOK}
	t.Setenv(daemon.EnvHome, f.dir)

	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != daemon.AuthScheme+" "+f.tok {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"detail":"invalid or expired client token"}`))
			return
		}
		if r.URL.Path == daemon.APIPrefix+"/status" {
			writeJSON(w, map[string]any{"service": daemon.ServiceID, "pid": os.Getpid()})
			return
		}
		raw, _ := io.ReadAll(io.LimitReader(r.Body, 1<<16))
		f.seenPath = r.Method + " " + r.URL.Path
		f.seenAuth = r.Header.Get("Authorization")
		f.seenBody = string(raw)

		if f.code != http.StatusOK {
			// A daemon that lacks the route says so; one that has it explains
			// itself instead. IsRouteMissing keys off exactly that difference.
			detail := "no usable credential store; set LEMONADE_AMD_API_KEY"
			if f.code == http.StatusNotFound {
				detail = "Not Found"
			}
			w.WriteHeader(f.code)
			writeJSON(w, map[string]any{"detail": detail})
			return
		}
		if strings.HasSuffix(r.URL.Path, "/authenticate") {
			writeJSON(w, map[string]any{"authenticated": true})
			return
		}
		writeJSON(w, map[string]any{"remembered": true})
	}))
	t.Cleanup(f.srv.Close)

	f.writeInstance()
	return f
}

func (f *fakeDaemon) writeInstance() {
	f.t.Helper()
	u, err := url.Parse(f.srv.URL)
	if err != nil {
		f.t.Fatalf("parse test server URL: %v", err)
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		f.t.Fatalf("parse test server port: %v", err)
	}
	raw, err := json.Marshal(&daemon.Instance{
		PID:        os.Getpid(),
		Port:       port,
		Token:      f.tok,
		Host:       daemon.DefaultHost,
		APIVersion: "1.1",
		Service:    daemon.ServiceID,
		StartedAt:  1.0,
	})
	if err != nil {
		f.t.Fatalf("marshal instance: %v", err)
	}
	if err := os.WriteFile(filepath.Join(f.dir, "instance.json"), raw, 0o600); err != nil {
		f.t.Fatalf("write instance.json: %v", err)
	}
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func TestRememberTokenSendsAnAuthenticatedJSONBody(t *testing.T) {
	f := newFakeDaemon(t)

	if err := rememberToken("s3cret"); err != nil {
		t.Fatalf("rememberToken: %v", err)
	}

	if want := "POST " + gatewayTokenPath; f.seenPath != want {
		t.Errorf("path = %q, want %q", f.seenPath, want)
	}
	if f.seenAuth != daemon.AuthScheme+" "+f.tok {
		t.Errorf("the daemon client token must guard the route, got %q", f.seenAuth)
	}
	// The daemon validates {"token": ...}; anything else is a 422 the user
	// would see as an unexplained failure.
	var body map[string]string
	if err := json.Unmarshal([]byte(f.seenBody), &body); err != nil {
		t.Fatalf("body is not JSON: %q", f.seenBody)
	}
	if body["token"] != "s3cret" {
		t.Errorf("body = %v, want the token under key \"token\"", body)
	}
}

func TestRememberTokenSurfacesTheDaemonRemedy(t *testing.T) {
	f := newFakeDaemon(t)
	f.code = http.StatusServiceUnavailable

	err := rememberToken("s3cret")
	if err == nil {
		t.Fatal("an unavailable credential store must not report success")
	}
	// The remedy is platform-specific and only the daemon knows it, so it has
	// to reach the user verbatim rather than being replaced.
	if !strings.Contains(err.Error(), "LEMONADE_AMD_API_KEY") {
		t.Errorf("the daemon's remedy must be passed through, got %q", err)
	}
}

func TestRememberTokenExplainsAnOldDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	f.code = http.StatusNotFound

	err := rememberToken("s3cret")
	if err == nil {
		t.Fatal("a missing route must not report success")
	}
	if !strings.Contains(err.Error(), "gaia gateway auth") {
		t.Errorf("a daemon without the route must name the workaround, got %q", err)
	}
}

func TestRestoreTokenReportsAuthentication(t *testing.T) {
	f := newFakeDaemon(t)

	if !restoreToken() {
		t.Fatal("restoreToken must report the daemon's authenticated=true")
	}
	if want := "POST " + gatewayAuthPath; f.seenPath != want {
		t.Errorf("path = %q, want %q", f.seenPath, want)
	}
	// Replaying a token must not require a body — the daemon reads it from the
	// credential store, and the token never crosses back into Go.
	if f.seenBody != "" {
		t.Errorf("authenticate must send no body, got %q", f.seenBody)
	}
}

func TestRestoreTokenIsQuietWithoutADaemon(t *testing.T) {
	// No instance.json: the common first-run case. It must not start a daemon
	// or report an error the user has to act on.
	t.Setenv(daemon.EnvHome, t.TempDir())

	if restoreToken() {
		t.Fatal("restoreToken must report false when no daemon is reachable")
	}
}
