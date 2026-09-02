package catalog

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// The flagship is what the TUI boots into, so it has to be launchable straight
// out of the seed list.
//
// It used to ship StatusComingSoon with "not published on the Agent Hub yet",
// which was already stale — gaia v0.1.1 IS published. With no daemon running
// the live catalog could not load, the seed stood, and a new user was shown
// "Available (1): Email" and no flagship at all.
func TestTheFlagshipShipsLaunchable(t *testing.T) {
	gaia := NewCatalog().Get(FlagshipID)
	if gaia == nil {
		t.Fatalf("the seed catalog no longer has a %q entry", FlagshipID)
	}
	if !gaia.Status.IsLaunchable() {
		t.Errorf("the flagship ships as %s; it is what the TUI opens into", gaia.Status)
	}
	if gaia.Transport != TransportSubprocess {
		t.Errorf("flagship transport = %v, want subprocess — the TUI spawns it itself", gaia.Transport)
	}
	if gaia.BinaryPath == "" {
		t.Error("the flagship names no binary, so nothing can be spawned or checked for")
	}
	if !gaia.CanonicalEvents {
		t.Error("the flagship is parsed as legacy events, which silently drops tool narration")
	}
}

// One agent on the launch path, and one reachable by id. Anything else in the
// seed list is a row nothing can start.
func TestTheSeedListIsTheTwoAgentsWeShip(t *testing.T) {
	var ids []string
	for _, a := range NewCatalog().All() {
		ids = append(ids, a.ID)
	}
	want := map[string]bool{FlagshipID: true, "email": true}
	if len(ids) != len(want) {
		t.Fatalf("seed ids = %v, want exactly %v", ids, want)
	}
	for _, id := range ids {
		if !want[id] {
			t.Errorf("seed catalog still carries %q, which nothing ships", id)
		}
	}
}

// --mock <path> is the claim that a runnable binary exists, so the rows it
// points at become launchable — otherwise the flag attaches to nothing now that
// no seed ships installed.
func TestSetMockBinaryMakesTheSubprocessAgentLaunchable(t *testing.T) {
	c := NewCatalog()
	c.SetMockBinary("/tmp/mock-agent")

	gaia := c.Get(FlagshipID)
	if !gaia.Status.IsLaunchable() {
		t.Errorf("with --mock, the flagship is %s, want launchable", gaia.Status)
	}
	if gaia.BinaryPath != "/tmp/mock-agent" {
		t.Errorf("flagship binary = %q, want the mock", gaia.BinaryPath)
	}
	// All three describe how to invoke ONE binary, so a mock replaces them as a
	// unit — inheriting the real agent's --dev would hand the mock an argument
	// it never declared.
	if len(gaia.DevArgs) != 0 || len(gaia.BinaryArgs) != 0 {
		t.Errorf("--mock left the real agent's args behind: %v %v", gaia.BinaryArgs, gaia.DevArgs)
	}
}

// The diagnostic a user actually reads when a binary IS there but unverified.
// "not found" would send them chasing a download that already happened, so the
// wording is the fix, not an implementation detail — pin it.
func TestResolveExecutableNamesAnUnverifiedInstall(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)        // os.UserHomeDir on POSIX
	t.Setenv("USERPROFILE", home) // ... and on Windows
	dir := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	// A name no real machine has on PATH, so exec.LookPath cannot pre-empt the
	// install-root lookup this is about.
	const name = "gaia-agent-unverified-fixture"
	file := name
	if runtime.GOOS == "windows" {
		file += ".exe"
	}
	if err := os.WriteFile(filepath.Join(dir, file), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}

	_, err := ResolveExecutable(name, "gaia")
	if err == nil {
		t.Fatal("an unverified binary in the install root resolved successfully")
	}
	// Naming the file is what separates "finish the install" from "go download it".
	for _, want := range []string{"gaia hub install", SentinelName, file} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error does not mention %q:\n%s", want, err)
		}
	}
}

// ResolveExecutable is what turns "the catalog names a binary" into "this
// process can exec it". A name that resolves nowhere must fail here — before a
// caller can report a connection.
func TestResolveExecutableRefusesAMissingBinary(t *testing.T) {
	_, err := ResolveExecutable("gaia-definitely-not-installed", FlagshipID)
	if err == nil {
		t.Fatal("a binary that is nowhere on this machine resolved successfully")
	}
	// GAIA ships one agent, so "build it from source" and "browse what the hub
	// publishes" were both the wrong next step. The installer is what puts the
	// binary there, and it is the only thing this may name.
	for _, want := range []string{"PATH", "cpp/build", "installer", InstallerURL} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error does not tell the user about %q:\n%s", want, err)
		}
	}
	if strings.Contains(err.Error(), "gaia tui list") {
		t.Errorf("the error still points at a hub browser that no longer exists:\n%s", err)
	}
}

func TestResolveExecutableFindsARealBinary(t *testing.T) {
	dir := t.TempDir()
	name := "fake-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := ResolveExecutable(path, FlagshipID)
	if err != nil {
		t.Fatalf("an executable file did not resolve: %v", err)
	}
	if got != path {
		t.Errorf("resolved to %q, want %q", got, path)
	}
}

// A path that exists but is not executable is exactly the half-installed state
// that used to surface as a first-message failure.
func TestResolveExecutableRefusesANonExecutableFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows carries no exec bit")
	}
	path := filepath.Join(t.TempDir(), "not-executable")
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := ResolveExecutable(path, FlagshipID); err == nil {
		t.Fatal("a non-executable file resolved successfully")
	}
}
