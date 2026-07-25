package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// sidecarSecret stands in for the sidecar bearer the daemon returns from
// /ensure. A thin client must never end up holding it.
const sidecarSecret = "SIDECAR-BEARER-MUST-NOT-LEAK"

// fakeDaemon is an httptest server that answers the daemon's control plane,
// paired with an instance.json in an isolated GAIA_DAEMON_HOME.
type fakeDaemon struct {
	t   *testing.T
	srv *httptest.Server
	dir string

	mu           sync.Mutex
	token        string
	reportedPID  int
	service      string
	statusCode   int
	ensureStatus int
	ensureDetail string
	authSeen     []string
	paths        []string
}

func newFakeDaemon(t *testing.T) *fakeDaemon {
	t.Helper()

	dir := t.TempDir()
	t.Setenv(EnvHome, dir)

	f := &fakeDaemon{
		t:            t,
		dir:          dir,
		token:        "token-A",
		reportedPID:  os.Getpid(),
		service:      ServiceID,
		statusCode:   http.StatusOK,
		ensureStatus: http.StatusOK,
	}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)

	if f.port() == ReservedPort {
		t.Fatalf("test server bound the reserved port %d", ReservedPort)
	}
	return f
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
	f.mu.Lock()
	token := f.token
	f.authSeen = append(f.authSeen, r.Header.Get("Authorization"))
	f.paths = append(f.paths, r.Method+" "+r.URL.Path)
	f.mu.Unlock()

	if r.Header.Get("Authorization") != AuthScheme+" "+token {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"invalid or expired client token"}`))
		return
	}

	switch {
	case r.URL.Path == APIPrefix+"/status":
		f.mu.Lock()
		code, service, pid := f.statusCode, f.service, f.reportedPID
		f.mu.Unlock()
		if code != http.StatusOK {
			w.WriteHeader(code)
			return
		}
		writeJSON(w, map[string]any{"service": service, "pid": pid, "api_version": DAEMONAPIVersionForTest})

	case strings.HasSuffix(r.URL.Path, "/ensure"):
		f.mu.Lock()
		code, detail := f.ensureStatus, f.ensureDetail
		f.mu.Unlock()
		if code != http.StatusOK {
			w.WriteHeader(code)
			writeJSON(w, map[string]any{"detail": detail})
			return
		}
		// The real daemon's ensure response carries the sidecar bearer; the
		// client must discard it.
		writeJSON(w, map[string]any{"port": 51999, "token": sidecarSecret, "state": "running"})

	default:
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]any{"detail": "no route " + r.URL.Path})
	}
}

// DAEMONAPIVersionForTest is what the fake reports in its status body. The client
// reads the contract version from instance.json, not from the probe, so this is
// only cosmetic.
const DAEMONAPIVersionForTest = "1.1"

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

// writeInstance persists an instance.json into the fake's home directory.
func (f *fakeDaemon) writeInstance(mutate func(*Instance)) *Instance {
	f.t.Helper()

	f.mu.Lock()
	inst := &Instance{
		PID:        os.Getpid(),
		Port:       f.port(),
		Token:      f.token,
		Host:       DefaultHost,
		APIVersion: "1.1",
		Service:    ServiceID,
		StartedAt:  1.0,
	}
	f.mu.Unlock()

	if mutate != nil {
		mutate(inst)
	}
	raw, err := json.MarshalIndent(inst, "", "  ")
	if err != nil {
		f.t.Fatalf("marshal instance: %v", err)
	}
	if err := os.WriteFile(filepath.Join(f.dir, "instance.json"), raw, 0o600); err != nil {
		f.t.Fatalf("write instance.json: %v", err)
	}
	return inst
}

func (f *fakeDaemon) rotateToken(next string) {
	f.mu.Lock()
	f.token = next
	f.mu.Unlock()
	f.writeInstance(func(i *Instance) { i.Token = next })
}

func (f *fakeDaemon) sawPath(want string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, p := range f.paths {
		if p == want {
			return true
		}
	}
	return false
}

// testClient builds a Client with fast timeouts and no real launcher.
func testClient(t *testing.T, mutate func(*Options)) *Client {
	t.Helper()
	opts := Options{
		ProbeTimeout:  2 * time.Second,
		StartTimeout:  3 * time.Second,
		EnsureTimeout: 5 * time.Second,
		StartCommand: func(context.Context) (*exec.Cmd, error) {
			return nil, &StartError{Reason: "no launcher was configured for this test"}
		},
		Logf: func(format string, args ...any) { t.Logf(format, args...) },
	}
	if mutate != nil {
		mutate(&opts)
	}
	return New(opts)
}

// --- trust checks -----------------------------------------------------------

func TestAttachHappyPath(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	inst, err := testClient(t, nil).Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	if inst.Port != f.port() || inst.Token != "token-A" {
		t.Errorf("unexpected instance: %+v", inst)
	}
}

func TestAttachMissingInstanceFile(t *testing.T) {
	newFakeDaemon(t) // sets GAIA_DAEMON_HOME, writes nothing

	_, err := testClient(t, nil).Attach(context.Background())
	var nr *NotRunningError
	if !asError(err, &nr) {
		t.Fatalf("expected *NotRunningError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "gaia daemon start") {
		t.Errorf("error must name the remedy: %v", err)
	}
}

func TestAttachMalformedInstanceFile(t *testing.T) {
	f := newFakeDaemon(t)
	if err := os.WriteFile(filepath.Join(f.dir, "instance.json"), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}

	_, err := testClient(t, nil).Attach(context.Background())
	var se *StaleError
	if !asError(err, &se) {
		t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "gaia daemon restart") {
		t.Errorf("error must name the remedy: %v", err)
	}
}

func TestAttachDeadPID(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, func(o *Options) {
		o.PIDAlive = func(int) bool { return false }
	})
	_, err := c.Attach(context.Background())
	var se *StaleError
	if !asError(err, &se) {
		t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "not running") {
		t.Errorf("error must say the pid is dead: %v", err)
	}
	// The probe must not even be attempted once the pid is known dead.
	if f.sawPath("GET " + APIPrefix + "/status") {
		t.Error("a dead pid must short-circuit before probing")
	}
}

func TestAttachPIDMismatch(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	f.mu.Lock()
	f.reportedPID = os.Getpid() + 100000 // a different process answers the port
	f.mu.Unlock()

	_, err := testClient(t, nil).Attach(context.Background())
	var se *StaleError
	if !asError(err, &se) {
		t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "registry records pid") {
		t.Errorf("error must name the pid mismatch: %v", err)
	}
}

func TestAttachWrongService(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	f.mu.Lock()
	f.service = "some-other-server"
	f.mu.Unlock()

	_, err := testClient(t, nil).Attach(context.Background())
	var se *StaleError
	if !asError(err, &se) {
		t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "took the freed port") {
		t.Errorf("error must explain the recycled port: %v", err)
	}
}

func TestAttachStatusNon200(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	f.mu.Lock()
	f.statusCode = http.StatusInternalServerError
	f.mu.Unlock()

	_, err := testClient(t, nil).Attach(context.Background())
	var se *StaleError
	if !asError(err, &se) {
		t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
	}
}

func TestAttachMajorVersionMismatch(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(func(i *Instance) { i.APIVersion = "2.0" })

	_, err := testClient(t, nil).Attach(context.Background())
	var ve *VersionError
	if !asError(err, &ve) {
		t.Fatalf("expected *VersionError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "gaia daemon restart") {
		t.Errorf("error must name the remedy: %v", err)
	}
}

func TestAttachUnparsableVersion(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(func(i *Instance) { i.APIVersion = "nightly" })

	_, err := testClient(t, nil).Attach(context.Background())
	var ve *VersionError
	if !asError(err, &ve) {
		t.Fatalf("expected *VersionError, got %#v (%v)", err, err)
	}
}

func TestEnsureAgentRejectsMinorBelowAgentsFloor(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(func(i *Instance) { i.APIVersion = "1.0" })

	// A pre-#2142 daemon passes the MAJOR gate, so plain Attach succeeds …
	if _, err := testClient(t, nil).Attach(context.Background()); err != nil {
		t.Fatalf("Attach on v1.0 should pass the MAJOR gate: %v", err)
	}
	// … but every agents route would 404, so EnsureAgent must refuse.
	_, err := testClient(t, nil).EnsureAgent(context.Background(), "email")
	var ve *VersionError
	if !asError(err, &ve) {
		t.Fatalf("expected *VersionError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "v1.1+") {
		t.Errorf("error must name the required floor: %v", err)
	}
	if f.sawPath("POST " + APIPrefix + "/agents/email/ensure") {
		t.Error("the ensure call must not be attempted below the agents floor")
	}
}

func TestAttachRejectsReservedPort(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(func(i *Instance) { i.Port = ReservedPort })

	_, err := testClient(t, nil).Attach(context.Background())
	var se *StaleError
	if !asError(err, &se) {
		t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), strconv.Itoa(ReservedPort)) {
		t.Errorf("error must name the reserved port: %v", err)
	}
}

func TestReadInstanceRejectsIncompleteRecords(t *testing.T) {
	f := newFakeDaemon(t)

	cases := []struct {
		name   string
		mutate func(*Instance)
		want   string
	}{
		{"no pid", func(i *Instance) { i.PID = 0 }, "no usable pid"},
		{"no token", func(i *Instance) { i.Token = "" }, "no client token"},
		{"no api_version", func(i *Instance) { i.APIVersion = "" }, "no api_version"},
		{"foreign service", func(i *Instance) { i.Service = "not-gaia" }, "written by service"},
		{"bad port", func(i *Instance) { i.Port = 70000 }, "invalid port"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f.writeInstance(tc.mutate)
			_, err := ReadInstance()
			var se *StaleError
			if !asError(err, &se) {
				t.Fatalf("expected *StaleError, got %#v (%v)", err, err)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Errorf("error %q must mention %q", err, tc.want)
			}
		})
	}
}

// --- token rotation ---------------------------------------------------------

func TestDoRetriesOnceAfterTokenRotation(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}

	// The daemon restarted: it now requires token-B and instance.json records
	// token-B, while the caller still holds the token-A instance in memory.
	f.rotateToken("token-B")

	resp, fresh, err := c.Do(context.Background(), inst, Request{
		Method: http.MethodGet,
		Path:   APIPrefix + "/status",
	})
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("after the retry, status = %d, want 200", resp.StatusCode)
	}
	if fresh.Token != "token-B" {
		t.Errorf("returned instance still carries the old token")
	}
	f.mu.Lock()
	auths := append([]string(nil), f.authSeen...)
	f.mu.Unlock()
	if !containsAuth(auths, "token-A") || !containsAuth(auths, "token-B") {
		t.Errorf("expected one attempt per token, saw %d requests", len(auths))
	}
}

func TestDoFailsWhenTokenDidNotRotate(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	c := testClient(t, nil)
	inst, err := c.Attach(context.Background())
	if err != nil {
		t.Fatalf("Attach: %v", err)
	}
	// The server rejects everything but instance.json still records token-A, so a
	// retry could only 401 again.
	f.mu.Lock()
	f.token = "token-Z"
	f.mu.Unlock()

	_, _, err = c.Do(context.Background(), inst, Request{
		Method: http.MethodGet,
		Path:   APIPrefix + "/status",
	})
	if err == nil {
		t.Fatal("expected an error for an unrotated 401")
	}
	if !strings.Contains(err.Error(), "same token") {
		t.Errorf("error must explain the unrotated token: %v", err)
	}
}

// --- ensure -----------------------------------------------------------------

func TestEnsureAgentNeverReturnsTheSidecarToken(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)

	inst, err := testClient(t, nil).EnsureAgent(context.Background(), "email")
	if err != nil {
		t.Fatalf("EnsureAgent: %v", err)
	}
	if inst.Token != "token-A" {
		t.Errorf("client must keep presenting the DAEMON token, got %q", inst.Token)
	}
	if strings.Contains(inst.String(), sidecarSecret) {
		t.Error("the sidecar bearer leaked into the instance")
	}
	if !f.sawPath("POST " + APIPrefix + "/agents/email/ensure") {
		t.Error("ensure was never called")
	}
}

func TestEnsureAgentSurfacesDaemonRefusal(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	f.mu.Lock()
	f.ensureStatus = http.StatusServiceUnavailable
	f.ensureDetail = "agent 'email' is not installed; run `gaia hub install email`"
	f.mu.Unlock()

	_, err := testClient(t, nil).EnsureAgent(context.Background(), "email")
	if err == nil {
		t.Fatal("expected an error when the daemon refuses to ensure")
	}
	if !strings.Contains(err.Error(), "gaia hub install email") {
		t.Errorf("the daemon's actionable detail must be surfaced verbatim: %v", err)
	}
}

func TestInstanceStringRedactsToken(t *testing.T) {
	inst := &Instance{PID: 42, Port: 5000, Token: "super-secret", Host: DefaultHost, APIVersion: "1.1", Service: ServiceID}
	got := fmt.Sprintf("%v / %s", inst, inst)
	if strings.Contains(got, "super-secret") {
		t.Fatalf("token leaked through String(): %s", got)
	}
	if !strings.Contains(got, "<redacted>") {
		t.Errorf("expected a redaction marker, got %s", got)
	}
}

// --- start-or-attach --------------------------------------------------------

// TestHelperProcess doubles as a fake `gaia daemon start`: it writes the
// instance.json handed to it via the environment, then exits.
func TestHelperProcess(t *testing.T) {
	if os.Getenv("GAIA_TUI_TEST_LAUNCHER") != "1" {
		return
	}
	defer os.Exit(0)

	if payload := os.Getenv("GAIA_TUI_TEST_INSTANCE"); payload != "" {
		path := filepath.Join(os.Getenv(EnvHome), "instance.json")
		if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
			fmt.Fprintf(os.Stderr, "helper: %v\n", err)
			os.Exit(2)
		}
	}
	if code := os.Getenv("GAIA_TUI_TEST_EXIT"); code != "" {
		fmt.Fprintln(os.Stderr, "helper: simulated launcher failure")
		n, _ := strconv.Atoi(code)
		os.Exit(n)
	}
}

func launcher(t *testing.T, dir, payload, exitCode string) func(context.Context) (*exec.Cmd, error) {
	t.Helper()
	return func(ctx context.Context) (*exec.Cmd, error) {
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHelperProcess$")
		cmd.Env = append(os.Environ(),
			"GAIA_TUI_TEST_LAUNCHER=1",
			"GAIA_TUI_TEST_INSTANCE="+payload,
			"GAIA_TUI_TEST_EXIT="+exitCode,
			EnvHome+"="+dir,
		)
		return cmd, nil
	}
}

func TestStartOrAttachSpawnsWhenNothingIsRegistered(t *testing.T) {
	f := newFakeDaemon(t)

	// The launcher registers an instance pointing at the fake server.
	inst := &Instance{
		PID: os.Getpid(), Port: f.port(), Token: "token-A",
		Host: DefaultHost, APIVersion: "1.1", Service: ServiceID,
	}
	payload, err := json.Marshal(inst)
	if err != nil {
		t.Fatal(err)
	}

	c := testClient(t, func(o *Options) {
		o.StartCommand = launcher(t, f.dir, string(payload), "")
	})
	got, err := c.StartOrAttach(context.Background())
	if err != nil {
		t.Fatalf("StartOrAttach: %v", err)
	}
	if got.Port != f.port() {
		t.Errorf("attached to port %d, want %d", got.Port, f.port())
	}
}

func TestStartOrAttachSurfacesLauncherFailure(t *testing.T) {
	f := newFakeDaemon(t)

	c := testClient(t, func(o *Options) {
		o.StartCommand = launcher(t, f.dir, "", "3")
	})
	_, err := c.StartOrAttach(context.Background())
	var se *StartError
	if !asError(err, &se) {
		t.Fatalf("expected *StartError, got %#v (%v)", err, err)
	}
	if !strings.Contains(err.Error(), "simulated launcher failure") {
		t.Errorf("the launcher's own output must be quoted back: %v", err)
	}
}

func TestStartOrAttachRefusesToKillALiveButUnresponsiveDaemon(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(nil)
	f.mu.Lock()
	f.statusCode = http.StatusInternalServerError
	f.mu.Unlock()

	c := testClient(t, func(o *Options) {
		o.StartCommand = func(context.Context) (*exec.Cmd, error) {
			t.Error("a live-but-unresponsive daemon must not be replaced silently")
			return nil, &StartError{Reason: "unreachable"}
		}
	})
	_, err := c.StartOrAttach(context.Background())
	if err == nil {
		t.Fatal("expected an error")
	}
	if !strings.Contains(err.Error(), "gaia daemon restart") {
		t.Errorf("error must tell the user how to reclaim it: %v", err)
	}
}

func TestStartOrAttachPropagatesVersionSkewWithoutSpawning(t *testing.T) {
	f := newFakeDaemon(t)
	f.writeInstance(func(i *Instance) { i.APIVersion = "9.0" })

	c := testClient(t, func(o *Options) {
		o.StartCommand = func(context.Context) (*exec.Cmd, error) {
			t.Error("a version skew must not trigger a second daemon")
			return nil, &StartError{Reason: "unreachable"}
		}
	})
	_, err := c.StartOrAttach(context.Background())
	var ve *VersionError
	if !asError(err, &ve) {
		t.Fatalf("expected *VersionError, got %#v (%v)", err, err)
	}
}

// --- primitives -------------------------------------------------------------

func TestPIDAlive(t *testing.T) {
	if !PIDAlive(os.Getpid()) {
		t.Error("the test process must read as alive")
	}
	if PIDAlive(0) || PIDAlive(-1) {
		t.Error("non-positive pids must read as dead")
	}

	// A reaped child is the canonical dead pid.
	cmd := exec.Command(os.Args[0], "-test.run=^TestHelperProcess$")
	cmd.Env = append(os.Environ(), "GAIA_TUI_TEST_LAUNCHER=1")
	if err := cmd.Run(); err != nil {
		t.Fatalf("run helper: %v", err)
	}
	if PIDAlive(cmd.Process.Pid) {
		t.Errorf("reaped pid %d must read as dead", cmd.Process.Pid)
	}
}

func TestStartLockIsExclusive(t *testing.T) {
	path := filepath.Join(t.TempDir(), "instance.lock")

	first, err := acquireLock(path, time.Second)
	if err != nil {
		t.Fatalf("first acquireLock: %v", err)
	}

	if _, err := acquireLock(path, 300*time.Millisecond); err == nil {
		t.Fatal("a second holder must not get the lock while the first holds it")
	} else if !strings.Contains(err.Error(), "start lock") {
		t.Errorf("error must name the lock: %v", err)
	}

	first.release()

	second, err := acquireLock(path, time.Second)
	if err != nil {
		t.Fatalf("acquireLock after release: %v", err)
	}
	second.release()
}

func TestParseAPIVersion(t *testing.T) {
	cases := []struct {
		in           string
		major, minor int
		wantErr      bool
	}{
		{"1.1", 1, 1, false},
		{"1.0", 1, 0, false},
		{"1", 1, 0, false},
		{"2.14", 2, 14, false},
		{"1.x", 1, 0, false},
		{"", 0, 0, true},
		{"beta", 0, 0, true},
	}
	for _, tc := range cases {
		major, minor, err := parseAPIVersion(tc.in)
		if tc.wantErr {
			if err == nil {
				t.Errorf("parseAPIVersion(%q): expected an error", tc.in)
			}
			continue
		}
		if err != nil {
			t.Errorf("parseAPIVersion(%q): %v", tc.in, err)
			continue
		}
		if major != tc.major || minor != tc.minor {
			t.Errorf("parseAPIVersion(%q) = %d.%d, want %d.%d", tc.in, major, minor, tc.major, tc.minor)
		}
	}
}

// --- helpers ----------------------------------------------------------------

// asError is errors.As with the generic plumbing inlined so each test reads as
// one line.
func asError[T error](err error, target *T) bool {
	for err != nil {
		if t, ok := err.(T); ok {
			*target = t
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

func containsAuth(seen []string, token string) bool {
	for _, s := range seen {
		if s == AuthScheme+" "+token {
			return true
		}
	}
	return false
}
