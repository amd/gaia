package test

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
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
func isolateGaiaHome(t *testing.T) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
}

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
