package catalog

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

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
		UnsupervisedFiltered: []string{"code"},
	}
}

func TestApplyHubCatalogMakesAnAvailableAgentInstallable(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(hubResponse(false, true))

	email := c.Get("email")
	if email == nil {
		t.Fatal("email disappeared from the catalog")
	}
	if !email.Installable() {
		t.Fatalf("email is not installable after the hub merge: %+v", email)
	}
	if email.DownloadSizeBytes == 0 || email.SecurityTier != TierExperimental {
		t.Errorf("hub metadata was not merged: %+v", email)
	}
	if !email.RequiresTrust() {
		t.Error("an experimental agent must require trust")
	}
	if email.Version != "0.5.0" {
		t.Errorf("version = %q, want the hub's latest 0.5.0", email.Version)
	}
}

func TestApplyHubCatalogFlipsInstalledAgentsToLaunchable(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(hubResponse(true, true))

	email := c.Get("email")
	if !email.Status.IsLaunchable() {
		t.Fatalf("an installed agent is not launchable: %+v", email)
	}
	if email.Installable() {
		t.Error("an already-installed agent still reports as installable")
	}
	if !email.Uninstallable() {
		t.Error("an installed hub agent must be uninstallable")
	}
}

// An agent the daemon has no sidecar spec for could be downloaded and then
// never started. It must not read as Available.
func TestUnsupervisedHubAgentIsNotOffered(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(hubResponse(false, false))

	email := c.Get("email")
	if email.Installable() {
		t.Fatal("an unsupervised agent is offered for install")
	}
	if email.NotOfferedReason == "" {
		t.Error("no reason given for hiding it")
	}
}

func TestSeedAgentsAbsentFromTheHubLeaveAvailable(t *testing.T) {
	c := NewCatalog()
	before := c.Get("code")
	if before.Status != StatusAvailable {
		t.Fatalf("precondition: seed 'code' is %s, want available", before.Status)
	}

	c.ApplyHubCatalog(hubResponse(false, true))

	after := c.Get("code")
	if after.Status == StatusAvailable {
		t.Error("'code' is still Available but the hub cannot install it")
	}
	// The daemon reported it as filtered-for-lack-of-a-spec, so say that
	// rather than "not published".
	if after.NotOfferedReason == "" {
		t.Error("no reason recorded for 'code'")
	}
	if summarize := c.Get("summarize"); summarize.NotOfferedReason == "" {
		t.Error("no reason recorded for an agent the hub simply does not carry")
	}
}

func TestApplyHubCatalogDoesNotClobberAnActiveSession(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(hubResponse(true, true))
	c.SetStatus("email", StatusActive)

	c.ApplyHubCatalog(hubResponse(true, true))

	if got := c.Get("email").Status; got != StatusActive {
		t.Errorf("status = %s after a re-fetch, want the live session preserved (active)", got)
	}
}

func TestMarkInstalledAndRemoveRoundTrip(t *testing.T) {
	c := NewCatalog()
	c.ApplyHubCatalog(hubResponse(false, true))

	c.MarkInstalled("email", "0.5.0")
	if !c.Get("email").Status.IsLaunchable() {
		t.Fatal("MarkInstalled did not make the agent launchable")
	}

	c.Remove("email")
	email := c.Get("email")
	if email.InstalledVersion != "" {
		t.Errorf("Remove left installed_version = %q", email.InstalledVersion)
	}
	if !email.Installable() {
		t.Error("a removed hub agent should be installable again")
	}
}

func TestRequiresTrustTreatsUnknownTierAsUnsafe(t *testing.T) {
	if !(Agent{}).RequiresTrust() {
		t.Error("an agent with no security tier must require trust")
	}
	if (Agent{SecurityTier: TierVerified}).RequiresTrust() {
		t.Error("a verified agent must not require trust")
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
	if got := findInstalledBinaryIn(root, "email", "gaia-agent-email"); got == "" {
		t.Fatal("did not search <id>/bin/")
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
	if err := os.WriteFile(filepath.Join(build, "gaia-bash"), []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}

	t.Chdir(dir)
	if got := findBinaryInRepo("gaia-bash"); got == "" {
		t.Fatal("findBinaryInRepo cannot see a binary without a .exe suffix")
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
