package test

import (
	"bytes"
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

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// Issue #2483: `gaia tui run <id> --query …` used to hang forever with nothing
// on either stream when a precondition was unmet. These tests cover the two
// halves of the fix — the headless readiness check, and the deadline that
// catches what a readiness check cannot.

// lemonadeDown is the exact repro condition: background service and sidecar up,
// the local model server refusing connections.
func lemonadeDown() *gateTransport {
	g := readyGateTransport()
	g.initStatus = http.StatusServiceUnavailable
	g.initBody = map[string]any{
		"ready": false,
		"lemonade": map[string]any{
			"reachable": false, "base_url": "http://localhost:8000/api/v1",
			"min_version": "8.1.0",
		},
		"model": map[string]any{"id": "Gemma-4-E4B-it-GGUF", "present": false},
		"hint":  "Lemonade Server is not reachable at http://localhost:8000/api/v1",
	}
	return g
}

// flatten collapses every run of whitespace, so an assertion is about what was
// said rather than where a renderer happened to wrap it.
func flatten(s string) string { return strings.Join(strings.Fields(s), " ") }

func emailConfig() preflight.Config { return preflight.ConfigFor("email", "Email") }

// The one-shot must refuse, name the unmet precondition, and name the command
// that fixes it — the interactive gate is never rendered, because a script has
// nobody to press a key.
func TestOneShotReadinessRefusesAndNamesTheRemedy(t *testing.T) {
	var errW bytes.Buffer
	rep := ui.ReportReadiness(context.Background(), lemonadeDown(), emailConfig(), &errW)

	if !rep.Blocked() {
		t.Fatalf("an unreachable model server must block the run:\n%s", rep)
	}
	msg := flatten(errW.String())
	for _, want := range []string{
		"Local AI",                            // which precondition
		"not running at",                      // what is wrong with it
		"run: lemonade-server serve",          // how to fix it
		"Nothing was sent to the Email agent", // and that nothing half-ran
	} {
		if !strings.Contains(msg, want) {
			t.Errorf("the refusal is missing %q:\n%s", want, errW.String())
		}
	}
	// No keystroke advice can be acted on from a script, so the refusal must at
	// least end by telling the reader to re-run it.
	if !strings.Contains(msg, "re-run") {
		t.Errorf("the refusal never tells the caller what to do next:\n%s", errW.String())
	}
}

// The two paths must not drift: whatever the gate shows a human for a condition
// is what the one-shot prints for the same condition.
func TestOneShotRefusalMatchesTheInteractiveGate(t *testing.T) {
	var errW bytes.Buffer
	ui.ReportReadiness(context.Background(), lemonadeDown(), emailConfig(), &errW)
	headless := flatten(errW.String())

	d := newRootDriver(t, lemonadeDown(), 100, 40)
	d.launchEmail()
	gate := d.flat()

	blocker, ok := preflight.Check(context.Background(), lemonadeDown(), emailConfig()).Blocker()
	if !ok {
		t.Fatal("the check reported no blocker for an unreachable model server")
	}
	for _, want := range []string{blocker.Line, blocker.Detail, blocker.Remedy.Command} {
		if want == "" {
			t.Fatalf("the blocker row is missing a field the user needs: %+v", blocker)
		}
		if !strings.Contains(headless, flatten(want)) {
			t.Errorf("the one-shot refusal dropped %q:\n%s", want, errW.String())
		}
		if !strings.Contains(gate, flatten(want)) {
			t.Errorf("the gate no longer shows %q, so the two paths have drifted:\n%s", want, d.screen())
		}
	}
}

// A healthy machine must be left alone: nothing blocks, and the check prints
// nothing that could pollute a caller's log.
func TestOneShotReadinessIsSilentWhenEverythingIsReady(t *testing.T) {
	var errW bytes.Buffer
	rep := ui.ReportReadiness(context.Background(), readyGateTransport(), emailConfig(), &errW)

	if rep.Blocked() || !rep.Ready() {
		t.Fatalf("a healthy machine must pass the check:\n%s", rep)
	}
	if errW.String() != "" {
		t.Errorf("a passing check must stay quiet, got:\n%s", errW.String())
	}
}

// An indeterminate row is named but does not refuse — the same rule the gate
// follows, so a Lemonade that does not advertise its version cannot become a
// wall a script can never get past.
func TestOneShotReadinessProceedsPastAnUnverifiableRow(t *testing.T) {
	g := readyGateTransport()
	lemonade := g.initBody["lemonade"].(map[string]any)
	delete(lemonade, "compatible")
	delete(lemonade, "version")

	var errW bytes.Buffer
	rep := ui.ReportReadiness(context.Background(), g, emailConfig(), &errW)

	if rep.Blocked() {
		t.Fatalf("an unverifiable row must not refuse the run:\n%s", rep)
	}
	if !strings.Contains(errW.String(), "could not be verified") {
		t.Errorf("what went unproven must still be said:\n%s", errW.String())
	}
}

// --- a stand-in for the real daemon relay -----------------------------------

// relayDaemon is the daemon side of a one-shot: the control plane, the agent
// relay, and a /query that can be made to stall. It is paired with an
// instance.json in an isolated GAIA_DAEMON_HOME, so a test never touches — or
// starts — the developer's own daemon.
type relayDaemon struct {
	srv *httptest.Server
	// home is the isolated GAIA_DAEMON_HOME, for handing to a child process.
	home string
	opts relayOptions
	// release ends a stall when the test is over, so no handler outlives it.
	release chan struct{}

	mu sync.Mutex
	// queries counts POSTs to /v1/email/query — what proves a refusal really
	// sent nothing rather than sending and discarding the answer.
	queries int
}

func (d *relayDaemon) queryCount() int {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.queries
}

// relayOptions is the fake's whole configuration. It is fixed before the server
// starts: a handler runs on its own goroutine, so anything set afterwards would
// be a data race, not a test knob.
type relayOptions struct {
	// initStatus / initBody are the answer to GET /v1/email/init. The zero value
	// means "everything ready".
	initStatus int
	initBody   map[string]any
	// stallQuery holds the query stream open without ever sending a byte.
	stallQuery bool
}

func newRelayDaemon(t *testing.T, opts relayOptions) *relayDaemon {
	t.Helper()

	if opts.initStatus == 0 {
		opts.initStatus = http.StatusOK
	}
	if opts.initBody == nil {
		opts.initBody = readyGateTransport().initBody
	}
	d := &relayDaemon{home: t.TempDir(), opts: opts, release: make(chan struct{})}
	t.Setenv(daemon.EnvHome, d.home)

	d.srv = httptest.NewServer(http.HandlerFunc(d.handle))
	// Order matters: Close waits for in-flight handlers, so a stall has to be
	// released BEFORE it runs. Cleanups run last-registered-first.
	t.Cleanup(d.srv.Close)
	t.Cleanup(func() { close(d.release) })

	u, err := url.Parse(d.srv.URL)
	if err != nil {
		t.Fatalf("parse server URL: %v", err)
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatalf("parse server port: %v", err)
	}
	raw, err := json.Marshal(daemon.Instance{
		PID: os.Getpid(), Port: port, Token: "test-token",
		Host: daemon.DefaultHost, APIVersion: "1.1", Service: daemon.ServiceID,
	})
	if err != nil {
		t.Fatalf("marshal instance: %v", err)
	}
	if err := os.WriteFile(filepath.Join(d.home, "instance.json"), raw, 0o600); err != nil {
		t.Fatalf("write instance.json: %v", err)
	}
	return d
}

func (d *relayDaemon) handle(w http.ResponseWriter, r *http.Request) {
	switch {
	case r.URL.Path == daemon.APIPrefix+"/status":
		writeJSON(w, map[string]any{"service": daemon.ServiceID, "pid": os.Getpid()})

	case r.URL.Path == daemon.APIPrefix+"/agents":
		writeJSON(w, map[string]any{"agents": []map[string]any{
			{"agent_id": "email", "state": "running", "pid": os.Getpid(),
				"agent_version": "0.5.0", "api_version": "1.0"},
		}})

	case strings.HasSuffix(r.URL.Path, "/ensure"):
		writeJSON(w, map[string]any{"agent_id": "email", "state": "running"})

	case strings.HasSuffix(r.URL.Path, "/init"):
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(d.opts.initStatus)
		_ = json.NewEncoder(w).Encode(d.opts.initBody)

	case strings.HasSuffix(r.URL.Path, "/connectors"):
		writeJSON(w, readyGateTransport().connectors)

	case strings.HasSuffix(r.URL.Path, "/query"):
		d.mu.Lock()
		d.queries++
		d.mu.Unlock()
		if !d.opts.stallQuery {
			w.WriteHeader(http.StatusNotImplemented)
			writeJSON(w, map[string]any{"detail": "this fake only stalls"})
			return
		}
		// Accepted, headers flushed — and then silence. The relay's own read
		// watchdog is reset by any traffic, so nothing below the caller's
		// deadline would ever end this.
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		select {
		case <-d.release:
		case <-r.Context().Done():
		}

	default:
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]any{"detail": "no route " + r.URL.Path})
	}
}

// The whole command, as a user runs it: with the model server down it must exit
// non-zero in seconds, say what is wrong on stderr, and leave stdout empty.
func TestRunQueryRefusesInSecondsWhenAPreconditionIsUnmet(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	d := newRelayDaemon(t, relayOptions{
		initStatus: http.StatusServiceUnavailable,
		initBody:   lemonadeDown().initBody,
	})

	cmd := exec.Command(gaiaBin, "run", "email", "--query", "triage my inbox")
	cmd.Env = append(os.Environ(),
		"PATH="+binDir+string(os.PathListSeparator)+os.Getenv("PATH"),
		daemon.EnvHome+"="+d.home)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr

	started := time.Now()
	err := cmd.Run()
	elapsed := time.Since(started)

	if err == nil {
		t.Fatalf("an unmet precondition exited 0\nstderr:\n%s", stderr.String())
	}
	if elapsed > 30*time.Second {
		t.Errorf("took %s to refuse — a script cannot tell that from a hang", elapsed)
	}
	if stdout.String() != "" {
		t.Errorf("stdout must stay empty so `> answer.txt` never captures a failure, got %q", stdout.String())
	}
	for _, want := range []string{"not ready", "Local AI", "lemonade-server serve"} {
		if !strings.Contains(stderr.String(), want) {
			t.Errorf("stderr is missing %q:\n%s", want, stderr.String())
		}
	}
	if strings.Contains(stdout.String(), altScreenEnter) || strings.Contains(stderr.String(), altScreenEnter) {
		t.Error("the refusal opened the alt screen; a script has nobody to press a key")
	}
	// The refusal promises "Nothing was sent to the Email agent". Prove it.
	if n := d.queryCount(); n != 0 {
		t.Errorf("the run sent %d quer(ies) after refusing — the message is a lie", n)
	}
}

// The other half of the fix, end to end through the real command: a daemon that
// accepts the query and never answers must be abandoned at the bound, not waited
// on. --timeout is what makes that bound testable — and raisable by a caller
// whose agent legitimately runs longer than the default.
func TestRunQueryAbandonsAStalledAgentAtTheTimeout(t *testing.T) {
	gaiaBin, binDir := buildBinaries(t)

	d := newRelayDaemon(t, relayOptions{stallQuery: true})

	cmd := exec.Command(gaiaBin, "run", "email", "--query", "triage my inbox", "--timeout", "3s")
	cmd.Env = append(os.Environ(),
		"PATH="+binDir+string(os.PathListSeparator)+os.Getenv("PATH"),
		daemon.EnvHome+"="+d.home)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr

	started := time.Now()
	err := cmd.Run()
	elapsed := time.Since(started)

	if err == nil {
		t.Fatalf("a turn that never got an answer exited 0\nstderr:\n%s", stderr.String())
	}
	if elapsed > 60*time.Second {
		t.Errorf("took %s to abandon a 3s turn — the bound is not being applied", elapsed)
	}
	if !strings.Contains(stderr.String(), "gave up") {
		t.Errorf("stderr does not report the deadline:\n%s", stderr.String())
	}
	if stdout.String() != "" {
		t.Errorf("stdout = %q, want empty", stdout.String())
	}
	if n := d.queryCount(); n != 1 {
		t.Errorf("the agent was queried %d times, want exactly 1", n)
	}
}

// The case a readiness check alone does not cover, over the real SSE transport:
// the deadline is what turns a stalled stream into a reportable failure.
func TestOneShotAgainstAStallingDaemonHitsTheDeadline(t *testing.T) {
	newRelayDaemon(t, relayOptions{stallQuery: true})

	c := client.NewSSEClient("email", daemon.New(daemon.Options{
		PIDAlive: func(int) bool { return true },
		// A failed attach must never fall through to the production launcher and
		// start a real daemon on the machine running the tests.
		StartCommand: func(context.Context) (*exec.Cmd, error) {
			return nil, fmt.Errorf("this test must never start a real daemon")
		},
	}), client.SSEOptions{})
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	var out, errW bytes.Buffer
	done := make(chan ui.OneShotResult, 1)
	started := time.Now()
	go func() { done <- ui.RunOneShot(ctx, c, "triage my inbox", &out, &errW, nil) }()

	select {
	case res := <-done:
		if res.ExitCode == 0 {
			t.Errorf("a stalled turn exited 0; stderr:\n%s", errW.String())
		}
		if out.String() != "" {
			t.Errorf("stdout must stay empty when nothing was answered, got %q", out.String())
		}
		if !strings.Contains(errW.String(), "gave up") {
			t.Errorf("stderr does not report the deadline:\n%s", errW.String())
		}
		if elapsed := time.Since(started); elapsed > 30*time.Second {
			t.Errorf("took %s to give up on a 2s deadline", elapsed)
		}
	case <-time.After(60 * time.Second):
		t.Fatal("the one-shot hung against a stalling daemon — this is issue #2483")
	}
}
