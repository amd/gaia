package catalog

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// writeSentinel marks <root>/<agentID> a completed install, which is what
// findInstalledBinaryIn requires before it will hand back a binary from there.
func writeSentinel(t *testing.T, root, agentID, executable string) {
	t.Helper()
	dir := filepath.Join(root, agentID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := `{"id":"` + agentID + `","version":"0.1.0","language":"python","executable":"` + executable + `"}`
	if err := os.WriteFile(filepath.Join(dir, SentinelName), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func hubResponse(installed bool, supervised bool) *HubCatalog {
	entry := HubEntry{
		ID:                "email",
		Name:              "Email",
		Description:       "Email triage, drafting, and calendar",
		Category:          "Productivity",
		Icon:              "📧",
		Author:            "AMD",
		SecurityTier:      TierExperimental,
		Permissions:       []string{"gmail:read", "gmail:send"},
		DownloadSizeBytes: 39845888,
		LatestVersion:     "0.5.0",
		Installed:         installed,
		Supervised:        supervised,
	}
	if installed {
		entry.InstalledVersion = "0.5.0"
	}
	return &HubCatalog{
		Agents:               []HubEntry{entry},
		UnsupervisedFiltered: []string{"chat"},
	}
}

// "unknown" must never read as "safe": an entry with no tier needs the same
// explicit opt-in a community one does.
func TestRequiresTrustTreatsUnknownTierAsUnsafe(t *testing.T) {
	for tier, want := range map[string]bool{
		TierVerified:     false,
		TierCommunity:    true,
		TierExperimental: true,
		"":               true,
		"something-new":  true,
	} {
		if got := (HubEntry{SecurityTier: tier}).RequiresTrust(); got != want {
			t.Errorf("tier %q RequiresTrust() = %v, want %v", tier, got, want)
		}
	}
}

func TestFormatSize(t *testing.T) {
	cases := map[int64]string{
		0:        "unknown size",
		512:      "512 B",
		2048:     "2 KB",
		39845888: "38.0 MB",
	}
	for in, want := range cases {
		if got := FormatSize(in); got != want {
			t.Errorf("FormatSize(%d) = %q, want %q", in, got, want)
		}
	}
}

// §5 bug 9: the old lookup only checked cpp/build/*/<name>.exe, so it could
// never find a Python or frozen agent on macOS or Linux.
func TestFindInstalledBinaryFindsANonWindowsExecutable(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "email")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	name := "gaia-agent-email"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeSentinel(t, root, "email", name)

	got := findInstalledBinaryIn(root, "email", "gaia-agent-email")
	if got == "" {
		t.Fatalf("did not find the installed binary at %s", path)
	}
	if !filepath.IsAbs(got) {
		t.Errorf("returned a relative path %q", got)
	}
}

func TestFindInstalledBinarySearchesTheBinSubdir(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "email", "bin")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "gaia-agent-email"), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeSentinel(t, root, "email", "gaia-agent-email")
	if got := findInstalledBinaryIn(root, "email", "gaia-agent-email"); got == "" {
		t.Fatal("did not search <id>/bin/")
	}
}

// The flagship's stdio child and the frozen REST sidecar are both named
// `gaia-agent`, and other installers stage the REST one into this same
// directory without a sentinel. Spawning it fed uvicorn's startup log to a JSON
// line scanner, which the user saw as "unreadable event skipped" (#3062).
func TestFindInstalledBinaryIgnoresABinaryWithNoSentinel(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "gaia")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	name := "gaia-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}

	if got := findInstalledBinaryIn(root, "gaia", "gaia-agent"); got != "" {
		t.Errorf("ran an unverified binary from the install root: %s", got)
	}
	// The file is still there, so the diagnostic must say "finish the install"
	// rather than sending the user to look for a download that already happened.
	if got := installDirBinaryIn(root, "gaia", "gaia-agent"); got == "" {
		t.Error("installDirBinaryIn must still see the file, for the diagnostic")
	}
}

// A corrupt sentinel is not proof of a completed install either.
func TestFindInstalledBinaryIgnoresACorruptSentinel(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "gaia")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	name := "gaia-agent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, SentinelName), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := findInstalledBinaryIn(root, "gaia", "gaia-agent"); got != "" {
		t.Errorf("trusted a corrupt sentinel: %s", got)
	}
}

func TestFindInstalledBinaryIgnoresANonExecutableFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows mode bits carry no exec information")
	}
	root := t.TempDir()
	dir := filepath.Join(root, "email")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "gaia-agent-email"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	// Sentinel present on purpose: without it the install-completeness gate
	// would reject this, and the test would pass without ever reaching the mode
	// bits it exists to check.
	writeSentinel(t, root, "email", "gaia-agent-email")
	if got := findInstalledBinaryIn(root, "email", "gaia-agent-email"); got != "" {
		t.Errorf("returned a non-executable file: %s", got)
	}
}

func TestFindBinaryInRepoFindsANonExeBuildOutput(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("this is the macOS/Linux half of bug 9")
	}
	dir := t.TempDir()
	build := filepath.Join(dir, "cpp", "build", "Release")
	if err := os.MkdirAll(build, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(build, "gaia-agent"), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}

	t.Chdir(dir)
	if got := findBinaryInRepo("gaia-agent"); got == "" {
		t.Fatal("findBinaryInRepo cannot see a binary without a .exe suffix")
	}
}

// The flagship agent is the only seeded entry declared TransportSubprocess, so
// it is the only one whose transport the sentinel path had to correct -- which
// is why installing it shipped a TUI that spawned the frozen REST sidecar and
// tried to read newline-delimited JSON out of a uvicorn log.
func TestInstalledSeededSubprocessAgentBecomesDaemonTransport(t *testing.T) {
	sentinel := `{"id":"gaia","version":"0.1.1","language":"python","artifact_kind":"binary"}`
	home := t.TempDir()
	agentDir := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(agentDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(agentDir, SentinelName), []byte(sentinel), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	}

	c := NewCatalog()
	if seeded := c.Get("gaia"); seeded == nil || seeded.Transport != TransportSubprocess {
		t.Skip("gaia is no longer seeded as a subprocess agent; this regression cannot recur")
	}

	c.LoadInstalledAgents()

	gaia := c.Get("gaia")
	if gaia == nil {
		t.Fatal("gaia disappeared from the catalog after loading its sentinel")
	}
	if gaia.Transport != TransportDaemon {
		t.Errorf("transport = %v, want TransportDaemon: an installed hub agent is an "+
			"HTTP sidecar the daemon supervises, not a binary to spawn over stdio",
			gaia.Transport)
	}
}

// A wheel install can share the flagship id without being a daemon sidecar.
// The sentinel proves that the package is installed, but it must not override
// the seeded subprocess transport the flagship uses.
func TestInstalledSeededSubprocessAgentKeepsItsTransportForWheel(t *testing.T) {
	sentinel := `{"id":"gaia","version":"0.1.1","language":"python","artifact_kind":"wheel"}`
	home := t.TempDir()
	agentDir := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(agentDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(agentDir, SentinelName), []byte(sentinel), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	}

	c := NewCatalog()
	if seeded := c.Get("gaia"); seeded == nil || seeded.Transport != TransportSubprocess {
		t.Skip("gaia is no longer seeded as a subprocess agent; this regression cannot recur")
	}

	c.LoadInstalledAgents()

	gaia := c.Get("gaia")
	if gaia == nil {
		t.Fatal("gaia disappeared from the catalog after loading its sentinel")
	}
	if gaia.Transport != TransportSubprocess {
		t.Errorf("transport = %v, want TransportSubprocess: a wheel install must not turn the flagship into a daemon sidecar", gaia.Transport)
	}
}

func TestInstalledSeededSubprocessAgentKeepsItsTransportWithoutArtifactKind(t *testing.T) {
	sentinel := `{"id":"gaia","version":"0.1.1","language":"python"}`
	home := t.TempDir()
	agentDir := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(agentDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(agentDir, SentinelName), []byte(sentinel), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	}

	c := NewCatalog()
	if seeded := c.Get("gaia"); seeded == nil || seeded.Transport != TransportSubprocess {
		t.Skip("gaia is no longer seeded as a subprocess agent; this regression cannot recur")
	}

	c.LoadInstalledAgents()
	gaia := c.Get("gaia")
	if gaia == nil {
		t.Fatal("gaia disappeared from the catalog after loading its sentinel")
	}
	if gaia.Transport != TransportSubprocess {
		t.Errorf("transport = %v, want TransportSubprocess: missing artifact_kind defaults to a wheel install", gaia.Transport)
	}
}

func TestLoadInstalledAgentsReadsSentinels(t *testing.T) {
	// InstallRoot mirrors the Python installer exactly (<home>/.gaia/agents),
	// so the fixture is a fake home rather than an env override.
	sentinel := `{"id":"email","version":"0.5.0","language":"python","artifact_kind":"binary"}`
	home := t.TempDir()
	agentDir := filepath.Join(home, ".gaia", "agents", "email")
	if err := os.MkdirAll(agentDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(agentDir, SentinelName), []byte(sentinel), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	}

	c := NewCatalog()
	c.LoadInstalledAgents()

	email := c.Get("email")
	if email == nil || !email.Status.IsLaunchable() {
		t.Fatalf("an installed sentinel did not make email launchable: %+v", email)
	}
	if email.InstalledVersion != "0.5.0" {
		t.Errorf("installed version = %q, want 0.5.0", email.InstalledVersion)
	}
}
