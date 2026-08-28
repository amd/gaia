package test

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/gaiainit"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// The failure this gate exists to stop: the TUI opened a chat for an agent
// whose binary was never on the machine, painted "Connected to: GAIA", and only
// failed when the user sent their first message with
// `exec: "gaia-agent": executable file not found in $PATH`.
//
// A launch must verify the agent can start BEFORE any screen claims it did —
// and for the flagship that check used to be skipped entirely, because every
// row the gate knew how to ask was probed through a daemon it does not use.
func TestAMissingAgentBinaryHaltsAtPreflightAndNeverReachesChat(t *testing.T) {
	isolateGaiaHome(t)
	const missing = "gaia-agent-that-was-never-installed"
	d := newLocalDriver(t, missing, 120, 40)

	d.launch()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view = %q, want preflight — a chat opened for an agent that cannot start", got)
	}
	// Asserted from model state, not from the rendered text: the screen's
	// wording is allowed to change, the row key refusing the launch is not.
	if got := d.m.ControlSnapshot().Blocker; got != preflight.KeyBinary {
		t.Fatalf("blocker = %q, want %q", got, preflight.KeyBinary)
	}

	screen := d.flat()
	for _, want := range []string{
		missing,              // what is missing
		"Looked in",          // where it looked
		"installer",          // what to do about it
		catalog.InstallerURL, // where to go
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("the halt screen never says %q:\n%s", want, d.screen())
		}
	}

	// It must STAY halted: nothing may hand off to a chat after a hold tick or
	// a re-check. `r` is the advertised way to try again.
	d.send(key("r"))
	if got := d.view(); got == "chat" {
		t.Fatal("re-checking a still-missing binary opened the chat anyway")
	}
}

// The other half: an agent whose binary IS there passes the row. A check that
// refuses everything would be no better than one that refuses nothing.
func TestAResolvableBinaryPassesTheBinaryRow(t *testing.T) {
	isolateGaiaHome(t)
	stubSetupCheck(t, 0)
	// A port nothing serves, so the walk always stops at Local AI and the gate
	// is still on screen to be read. Without this the test passes or fails on
	// whether the DEVELOPER's machine happens to be running Lemonade: with it
	// up, the gate goes all-green and hands off to chat before the assertion.
	t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")

	d := newLocalDriver(t, mockBinaryPath(t), 120, 40)
	d.launch()

	row, ok := d.report().Find(preflight.KeyBinary)
	if !ok {
		t.Fatal("the local gate produced no agent-binary row")
	}
	if row.State != preflight.StateOK {
		t.Fatalf("a resolvable binary is %s: %s\n%s", row.State.Word(), row.Detail, d.screen())
	}
}

// --- helpers -----------------------------------------------------------------

// newLocalDriver builds the launch router around the flagship, with the local
// runner pointed at binary.
func newLocalDriver(t *testing.T, binary string, w, h int) *rootDriver {
	t.Helper()
	agent := catalog.NewCatalog().Get(catalog.FlagshipID)
	if agent == nil {
		t.Fatalf("the catalog has no %q entry", catalog.FlagshipID)
	}
	agent.BinaryPath = binary

	m := root.NewFlagshipModel(*agent, false).
		WithLocalPreflight(preflight.LocalOptions{Binary: binary}).
		WithPreflight(nil, preflight.Options{ReadyHold: time.Millisecond})
	d := &rootDriver{t: t, m: m, cat: catalog.NewCatalog()}
	d.send(windowSize(w, h))
	return d
}

// launch runs what the program runs on start: the splash, then the gate.
func (d *rootDriver) launch() {
	d.t.Helper()
	d.pump(d.m.Init())
}

// report is the gate's current answer, for asserting on rows rather than pixels.
func (d *rootDriver) report() preflight.Report {
	d.t.Helper()
	rep, ok := d.m.PreflightReport()
	if !ok {
		d.t.Fatalf("no readiness gate is on screen (view=%q)", d.view())
	}
	return rep
}

// isolateGaiaHome points the install-root lookup at a temp dir, so a developer
// box with a real ~/.gaia/agents cannot decide whether a "nothing is installed"
// test passes.
//
// It moves HOME, which is a blunt instrument: on Linux that is also where the
// Go toolchain keeps its module cache, so anything shelling out to `go build`
// after this re-downloads every dependency into the temp dir — and cleanup then
// fails, because module-cache files are read-only. buildBinaries is compiled
// once up front for exactly that reason; warm it here so the ordering cannot be
// got wrong by a caller that asks for the mock binary later.
func isolateGaiaHome(t *testing.T) {
	t.Helper()
	buildBinaries(t)
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	// GOMODCACHE/GOCACHE default to $HOME on Linux. Pin them to where they
	// already are, so a moved HOME cannot redirect them even if something does
	// shell out to the toolchain.
	t.Setenv("GOMODCACHE", goEnv(t, "GOMODCACHE"))
	t.Setenv("GOCACHE", goEnv(t, "GOCACHE"))
}

// goEnv reads one `go env` value, memoised.
//
// One variable per call rather than parsing a multi-value response: these are
// paths, and a Windows path routinely contains a space, so splitting on
// whitespace would corrupt exactly the machines this matters on.
func goEnv(t *testing.T, name string) string {
	t.Helper()
	goEnvMu.Lock()
	defer goEnvMu.Unlock()
	if got, ok := goEnvCache[name]; ok {
		return got
	}
	out, err := exec.Command("go", "env", name).Output()
	if err != nil {
		// Nothing to pin to. The caller's Setenv of "" is a no-op the toolchain
		// treats as "use the default", which is the behaviour without this.
		goEnvCache[name] = ""
		return ""
	}
	goEnvCache[name] = strings.TrimSpace(string(out))
	return goEnvCache[name]
}

var (
	goEnvMu    sync.Mutex
	goEnvCache = map[string]string{}
)

// stubSetupCheck replaces `gaia init` with a script that exits with code, so
// the model row answers without spawning a Python interpreter.
func stubSetupCheck(t *testing.T, code int) {
	t.Helper()
	dir := t.TempDir()
	var path string
	if runtime.GOOS == "windows" {
		path = filepath.Join(dir, "gaia-stub.bat")
		writeStub(t, path, fmt.Sprintf("@echo off\r\nexit /b %d\r\n", code))
	} else {
		path = filepath.Join(dir, "gaia-stub.sh")
		writeStub(t, path, fmt.Sprintf("#!/bin/sh\nexit %d\n", code))
	}
	orig := gaiainit.Binary
	gaiainit.Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { gaiainit.Binary = orig })
}

func writeStub(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
}

// The gate asked whether setup was ready and could not tell — `gaia` is not on
// PATH, so `gaia init --check` cannot answer. It said so and proceeded, which
// is right. What was wrong: the chat then re-ran the identical doomed probe and
// reported the identical finding a second time, as a red ERROR louder than the
// notice the gate had just given.
//
// One question, asked once. Suppression keys off the row having been PROBED,
// not off it having passed.
func TestAnIndeterminateSetupRowIsNotReAskedInTheChat(t *testing.T) {
	isolateGaiaHome(t)
	// Exit 2 is what an installed gaia older than --check returns, and a
	// missing gaia produces the same "unanswered" shape.
	stubSetupCheck(t, 2)
	t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")

	rep := preflight.NewLocalRunner(preflight.LocalOptions{
		Binary: mockBinaryPath(t), ClaudeMode: true, // claude mode: a down Lemonade must not block
	}).Check(context.Background(), preflight.ConfigFor("gaia", "GAIA"))

	row, ok := rep.Find(preflight.KeyModel)
	if !ok {
		t.Fatal("the local gate produced no AI model row")
	}
	if row.State != preflight.StateUnknown {
		t.Fatalf("test setup: model row is %s, want unknown", row.State.Word())
	}
	if rep.Blocked() {
		t.Fatal("test setup: an indeterminate row blocked the launch")
	}
	if !root.GateAskedAboutSetupForTest(rep) {
		t.Error("the chat will ask `gaia init --check` again and report the same " +
			"failure a second time, as an error")
	}
}

// A row the walk never reached IS still worth asking about: nothing was
// answered, so the chat's own check is the only one there is.
func TestASkippedSetupRowIsStillAskedInTheChat(t *testing.T) {
	isolateGaiaHome(t)
	rep := preflight.NewLocalRunner(preflight.LocalOptions{
		Binary: "gaia-agent-that-was-never-installed",
	}).Check(context.Background(), preflight.ConfigFor("gaia", "GAIA"))

	if root.GateAskedAboutSetupForTest(rep) {
		t.Error("a model row the walk never reached was treated as answered")
	}
}

// `gaia tui run email --model X` used to pass the model through. The
// interactive path was rewritten to build its client inside the router, which
// never received it — so the flag was accepted and the run quietly used the
// agent's default. Accepting a flag and changing nothing is the silent
// fallback the rules forbid, and the CLI reference still advertises it.
func TestTheModelOverrideReachesTheInteractiveClient(t *testing.T) {
	agent := catalog.Agent{
		ID: "email", Name: "Email", Transport: catalog.TransportDaemon,
	}
	m := root.NewFlagshipModel(agent, false).WithModel("some-other-model")

	if got := root.ModelForTest(m); got != "some-other-model" {
		t.Errorf("the router carries model %q; --model would be silently dropped", got)
	}
}
