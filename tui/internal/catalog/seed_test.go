package catalog

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// The hub must never offer an action the backend cannot honour. Nothing the
// Agent Hub does not publish may ship as installed: `gaia-bash` did, and on a
// machine that never built it the row connected and then failed on the first
// message with `exec: "gaia-bash": executable file not found in $PATH`.
func TestNoSeedAgentShipsAsInstalled(t *testing.T) {
	for _, a := range NewCatalog().All() {
		if a.Status.IsLaunchable() {
			t.Errorf("seed agent %q ships as %s; no unpublished agent may be launchable out of the box",
				a.ID, a.Status)
		}
	}
}

// gaia-bash is not a published hub agent, so it belongs under Coming Soon with
// a reason — not under Installed, and not under Available either.
func TestBashIsComingSoonAndUnlaunchable(t *testing.T) {
	bash := NewCatalog().Get("bash")
	if bash == nil {
		t.Fatal("the seed catalog no longer has a bash entry")
	}
	if bash.Status != StatusComingSoon {
		t.Errorf("bash status = %s, want coming soon", bash.Status)
	}
	if bash.Installable() {
		t.Error("bash is offered as installable; the daemon has no spec for it")
	}
	if bash.NotOfferedReason == "" {
		t.Error("bash is not offered and says nothing about why")
	}
}

// Only an agent the daemon can actually fetch AND start may sit under
// Available. Email is the one published sidecar today; every other seed has to
// wait for a hub row to promote it.
func TestOnlyPublishedSidecarsAreOfferedBeforeTheHubLoads(t *testing.T) {
	c := NewCatalog()
	var offered []string
	for _, a := range c.All() {
		if a.Status == StatusAvailable {
			offered = append(offered, a.ID)
		}
	}
	if len(offered) != 1 || offered[0] != "email" {
		t.Errorf("agents offered before the hub loads = %v, want [email]", offered)
	}
}

// A hub row the daemon supervises is what promotes an entry out of Coming Soon.
// Without this the seed change would make the hub permanently unable to offer
// anything new.
func TestHubCatalogPromotesASupervisedAgent(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(&HubCatalog{Agents: []HubEntry{{
		ID: "chat", LatestVersion: "1.2.3", Supervised: true, SecurityTier: TierVerified,
	}}})

	got := c.Get("chat")
	if got.Status != StatusAvailable {
		t.Fatalf("a supervised hub row left chat as %s, want available", got.Status)
	}
	if !got.Installable() {
		t.Error("a supervised, uninstalled hub row is not installable")
	}
	if got.NotOfferedReason != "" {
		t.Errorf("promoted entry still carries a not-offered reason: %q", got.NotOfferedReason)
	}
}

// Removing an entry the hub does not publish must put it back under Coming
// Soon. Sending it to Available would offer an install the daemon cannot serve.
func TestRemoveDoesNotPromoteAnUnpublishedAgent(t *testing.T) {
	c := NewCatalog()
	c.SetStatus("bash", StatusActive) // as if it had been launched
	c.Remove("bash")

	got := c.Get("bash")
	if got.Status != StatusComingSoon {
		t.Errorf("after Remove, bash is %s, want coming soon", got.Status)
	}
	if got.NotOfferedReason == "" {
		t.Error("after Remove, bash is not offered and says nothing about why")
	}
	if got.BinaryPath != "" {
		t.Errorf("after Remove, BinaryPath = %q, want empty", got.BinaryPath)
	}
}

// A hub agent the daemon CAN start goes back to Available, so it can be
// reinstalled from the same screen it was removed on.
func TestRemovePutsASupervisedHubAgentBackToAvailable(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(&HubCatalog{Agents: []HubEntry{{
		ID: "email", LatestVersion: "1.0.0", Supervised: true, Installed: true, InstalledVersion: "1.0.0",
	}}})
	c.Remove("email")

	if got := c.Get("email"); got.Status != StatusAvailable {
		t.Errorf("after Remove, a supervised hub agent is %s, want available", got.Status)
	}
}

// --mock <path> is the claim that a runnable binary exists, so the rows it
// points at become launchable — otherwise the flag attaches to nothing now that
// no seed ships installed.
func TestSetMockBinaryMakesTheSubprocessAgentLaunchable(t *testing.T) {
	c := NewCatalog()
	c.SetMockBinary("/tmp/mock-agent")

	bash := c.Get("bash")
	if !bash.Status.IsLaunchable() {
		t.Errorf("with --mock, bash is %s, want launchable", bash.Status)
	}
	if bash.BinaryPath != "/tmp/mock-agent" {
		t.Errorf("bash binary = %q, want the mock", bash.BinaryPath)
	}
	// An entry with no binary of its own has nothing for a mock to stand in for.
	if chat := c.Get("chat"); chat.Status.IsLaunchable() {
		t.Errorf("--mock made %q launchable; it declares no binary", chat.ID)
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
	_, err := ResolveExecutable("gaia-definitely-not-installed", "bash")
	if err == nil {
		t.Fatal("a binary that is nowhere on this machine resolved successfully")
	}
	for _, want := range []string{"PATH", "cpp/build", "gaia tui list"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the error does not tell the user about %q:\n%s", want, err)
		}
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

	got, err := ResolveExecutable(path, "bash")
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
	if _, err := ResolveExecutable(path, "bash"); err == nil {
		t.Fatal("a non-executable file resolved successfully")
	}
}
