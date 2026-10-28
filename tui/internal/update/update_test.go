// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func withHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	prev := homeDir
	homeDir = func() (string, error) { return home, nil }
	t.Cleanup(func() { homeDir = prev })
	return home
}

func TestNewerOrdersFinalsAndCandidates(t *testing.T) {
	cases := []struct {
		tag, current string
		want         bool
	}{
		{"v0.26.0", "0.25.0", true},
		{"v0.25.0", "0.25.0", false},
		{"v0.24.9", "0.25.0", false},
		{"v0.25.1", "0.25.1-rc.4", true},     // the final upgrades its candidate
		{"v0.25.1-rc5", "0.25.1-rc.4", true}, // a later candidate
		{"v0.25.1-rc4", "0.25.1", false},     // a candidate never beats its final
		{"v0.25.1-rc4", "0.25.1-rc.4", false},
		{"v0.26.0", "dev", false}, // a dev build is never behind
		{"latest", "0.25.0", false},
	}
	for _, c := range cases {
		if got := Newer(c.tag, c.current); got != c.want {
			t.Errorf("Newer(%q, %q) = %v, want %v", c.tag, c.current, got, c.want)
		}
	}
	if !SameVersion("v0.25.1-rc4", "0.25.1-rc.4") {
		t.Error("a tag and the binary's spelling of the same candidate must match")
	}
}

func TestValidTagMatchesTheInstallScripts(t *testing.T) {
	for _, ok := range []string{"v0.25.0", "v1.2.3", "v0.25.1-rc2"} {
		if !ValidTag(ok) {
			t.Errorf("ValidTag(%q) = false", ok)
		}
	}
	for _, bad := range []string{"0.25.0", "v0.25", "v0.25.0-rc0", "v0.25.0-beta", "v0.25.0/../x", "latest", ""} {
		if ValidTag(bad) {
			t.Errorf("ValidTag(%q) = true", bad)
		}
	}
}

func TestLatestSkipsCandidatesUnlessRunningOne(t *testing.T) {
	rs := []Release{{Tag: "v0.26.0-rc1", Prerelease: true}, {Tag: "v0.25.0"}}
	if r, _ := Latest(rs, "0.25.0", false); r.Tag != "v0.25.0" {
		t.Errorf("a final install was offered %s", r.Tag)
	}
	if r, _ := Latest(rs, "0.25.1-rc.1", false); r.Tag != "v0.26.0-rc1" {
		t.Errorf("an RC tester was offered %s", r.Tag)
	}
}

func TestConfigDefaultsOffAndKeepsOtherKeys(t *testing.T) {
	home := withHome(t)
	cfg, err := ReadConfig()
	if err != nil || cfg.AutoUpdate || cfg.PinnedVersion != "" {
		t.Fatalf("defaults = %+v, %v; want auto-update off, no pin", cfg, err)
	}
	path := filepath.Join(home, ".gaia", "update-config.json")
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path, []byte(`{"feedUrl":"https://feed","pinnedVersion":"v0.20.0"}`), 0o644)

	if err := SetAutoUpdate(true); err != nil {
		t.Fatal(err)
	}
	if err := SetPin("v0.24.0"); err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	data, _ := os.ReadFile(path)
	json.Unmarshal(data, &raw)
	want := map[string]any{"feedUrl": "https://feed", "pinnedVersion": "v0.20.0", "autoUpdate": true, "tuiPinnedVersion": "v0.24.0"}
	for k, v := range want {
		if raw[k] != v {
			t.Errorf("%s = %v, want %v (whole file: %s)", k, raw[k], v, data)
		}
	}
	cfg, _ = ReadConfig()
	if !cfg.AutoUpdate || cfg.PinnedVersion != "v0.24.0" {
		t.Errorf("read back %+v", cfg)
	}
	if err := SetPin(""); err != nil {
		t.Fatal(err)
	}
	if cfg, _ = ReadConfig(); cfg.PinnedVersion != "" {
		t.Errorf("unpin left %q", cfg.PinnedVersion)
	}
}

func TestBrokenConfigIsAnErrorNotTheDefaults(t *testing.T) {
	home := withHome(t)
	path := filepath.Join(home, ".gaia", "update-config.json")
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path, []byte(`{not json`), 0o644)
	if _, err := ReadConfig(); err == nil || !strings.Contains(err.Error(), path) {
		t.Fatalf("err = %v, want one naming %s", err, path)
	}
	if err := SetAutoUpdate(true); err == nil {
		t.Fatal("writing over a broken file must fail, not replace it")
	}
}

func serveReleases(t *testing.T, handler http.HandlerFunc) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	prev := apiBase
	apiBase = srv.URL
	t.Cleanup(func() { apiBase = prev })
}

func TestListReleasesDropsDraftsAndSortsNewestFirst(t *testing.T) {
	serveReleases(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]Release{
			{Tag: "v0.24.0"}, {Tag: "v0.26.0", Draft: true}, {Tag: "v0.25.1-rc2", Prerelease: true},
			{Tag: "v0.25.0"}, {Tag: "nightly"},
		})
	})
	rs, err := ListReleases(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	var tags []string
	for _, r := range rs {
		tags = append(tags, r.Tag)
	}
	if got := strings.Join(tags, ","); got != "v0.25.1-rc2,v0.25.0,v0.24.0" {
		t.Errorf("tags = %s", got)
	}
}

func TestListReleasesSaysWhyItFailed(t *testing.T) {
	serveReleases(t, func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "rate limited", http.StatusForbidden)
	})
	_, err := ListReleases(context.Background())
	if err == nil || !strings.Contains(err.Error(), "403") || !strings.Contains(err.Error(), ReleasesPage) {
		t.Fatalf("err = %v", err)
	}
}

func TestRateLimitSaysWhenAndHowToLiftIt(t *testing.T) {
	t.Setenv("GH_TOKEN", "")
	t.Setenv("GITHUB_TOKEN", "")
	serveReleases(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-RateLimit-Remaining", "0")
		w.Header().Set("X-RateLimit-Reset", "1792816995")
		http.Error(w, "rate limit exceeded", http.StatusForbidden)
	})
	_, err := ListReleases(context.Background())
	if err == nil || !strings.Contains(err.Error(), "hourly limit") || !strings.Contains(err.Error(), "GH_TOKEN") ||
		!strings.Contains(err.Error(), "resets at") {
		t.Fatalf("err = %v", err)
	}
}

func TestATokenIsSentWhenSet(t *testing.T) {
	t.Setenv("GH_TOKEN", "abc")
	var got string
	serveReleases(t, func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get("Authorization")
		w.Write([]byte("[]"))
	})
	if _, err := ListReleases(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got != "Bearer abc" {
		t.Errorf("Authorization = %q", got)
	}
}

func TestGetReleaseLooksUpAnyTag(t *testing.T) {
	serveReleases(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/releases/tags/v0.20.0" {
			http.NotFound(w, r)
			return
		}
		json.NewEncoder(w).Encode(Release{Tag: "v0.20.0"})
	})
	if r, err := GetRelease(context.Background(), "v0.20.0"); err != nil || r.Tag != "v0.20.0" {
		t.Fatalf("got %+v, %v", r, err)
	}
	if _, err := GetRelease(context.Background(), "v0.19.0"); err == nil || !strings.Contains(err.Error(), "no such release") {
		t.Fatalf("missing tag err = %v", err)
	}
	if _, err := GetRelease(context.Background(), "../../x"); err == nil {
		t.Fatal("a malformed tag must be refused before any request")
	}
}

func withExecutable(t *testing.T, path, os string) {
	t.Helper()
	prevExe, prevOS, prevSetup := executable, goos, windowsSetupDir
	executable = func() (string, error) { return path, nil }
	goos = os
	windowsSetupDir = func() string { return `C:\Programs\GAIA Terminal Hub` }
	t.Cleanup(func() { executable, goos, windowsSetupDir = prevExe, prevOS, prevSetup })
}

func TestDetectKind(t *testing.T) {
	home := withHome(t)
	scriptExe := filepath.Join(home, ".gaia", "bin", "gaia-tui")

	withExecutable(t, scriptExe, "linux")
	if k, err := DetectKind(); err != nil || k != KindScript {
		t.Errorf("~/.gaia/bin → %q, %v; want script", k, err)
	}
	withExecutable(t, `C:\Programs\GAIA Terminal Hub\gaia-tui.exe`, "windows")
	if k, _ := DetectKind(); k != KindWindowsSetup {
		t.Errorf("windows setup install → %q", k)
	}
	withExecutable(t, `D:\tools\gaia-tui.exe`, "windows")
	if _, err := DetectKind(); err == nil {
		t.Error("an exe outside the setup's folder must not be taken for a setup install")
	}
	withExecutable(t, "/usr/local/bin/gaia-tui", "darwin")
	if k, _ := DetectKind(); k != KindMacPkg {
		t.Errorf("macOS pkg install → %q", k)
	}
	withExecutable(t, filepath.Join(home, "src", "gaia-tui"), "linux")
	if _, err := DetectKind(); err == nil || !strings.Contains(err.Error(), "cannot update itself") {
		t.Errorf("a hand-built binary must refuse, got %v", err)
	}
}

func TestNewPlanNeedsAVerifiableInstaller(t *testing.T) {
	withHome(t)
	withExecutable(t, `C:\Programs\GAIA Terminal Hub\gaia-tui.exe`, "windows")
	setup := Asset{Name: "gaia-0.26.0-win-x64-setup.exe", URL: "https://x/setup.exe"}
	sums := Asset{Name: installerSums, URL: "https://x/sums"}

	if _, err := NewPlan(Release{Tag: "v0.26.0", Assets: []Asset{sums}}, false); err == nil {
		t.Error("a release without a Windows setup must be refused")
	}
	if _, err := NewPlan(Release{Tag: "v0.26.0", Assets: []Asset{setup}}, false); err == nil {
		t.Error("a release without checksums must be refused")
	}
	// The desktop app's own setup is not the GAIA setup.
	ui := Asset{Name: "gaia-agent-ui-0.26.0-x64-setup.exe"}
	if _, err := NewPlan(Release{Tag: "v0.26.0", Assets: []Asset{ui, sums}}, false); err == nil {
		t.Error("the Agent UI setup was taken for the GAIA setup")
	}
	p, err := NewPlan(Release{Tag: "v0.26.0", Assets: []Asset{setup, sums}}, true)
	if err != nil || p.Kind != KindWindowsSetup || !p.Pin {
		t.Fatalf("plan = %+v, %v", p, err)
	}
}

func TestScheduleAndCancel(t *testing.T) {
	t.Cleanup(Cancel)
	if _, ok := Pending(); ok {
		t.Fatal("nothing should be pending")
	}
	Schedule(Plan{Tag: "v0.26.0"})
	if p, ok := Pending(); !ok || p.Tag != "v0.26.0" {
		t.Fatalf("pending = %+v, %v", p, ok)
	}
	Cancel()
	if err := RunPending(context.Background(), nil, nil); err != nil {
		t.Fatalf("nothing pending must be a no-op, got %v", err)
	}
}

func TestChecksumFor(t *testing.T) {
	path := filepath.Join(t.TempDir(), installerSums)
	os.WriteFile(path, []byte("ABC123  gaia_0.26.0_amd64.deb\ndef456 *gaia-0.26.0.x86_64.rpm\n"), 0o644)
	if got, err := checksumFor(path, "gaia_0.26.0_amd64.deb"); err != nil || got != "abc123" {
		t.Errorf("deb = %q, %v", got, err)
	}
	if got, _ := checksumFor(path, "gaia-0.26.0.x86_64.rpm"); got != "def456" {
		t.Errorf("binary-mode line = %q", got)
	}
	if _, err := checksumFor(path, "other.pkg"); err == nil {
		t.Error("a file with no checksum must be refused")
	}
}

func TestSummary(t *testing.T) {
	prev := Current
	Current = "0.25.0"
	t.Cleanup(func() { Current = prev })
	st := Status{Current: "0.25.0", Latest: Release{Tag: "v0.26.0"}, Available: true}
	if s := st.Summary(); !strings.Contains(s, "v0.26.0 is available") || !strings.Contains(s, "Auto-update is off") {
		t.Errorf("summary = %q", s)
	}
	st.Config.PinnedVersion = "v0.24.0"
	if s := st.Summary(); !strings.Contains(s, "pinned v0.24.0") {
		t.Errorf("pinned summary = %q", s)
	}
}

func TestAFailedInstallKeepsThePin(t *testing.T) {
	withHome(t)
	if err := SetPin("v0.20.0"); err != nil {
		t.Fatal(err)
	}
	// A package kind whose installer is missing fails before running anything.
	Schedule(Plan{Tag: "v0.26.0", Kind: KindDeb, Release: Release{Tag: "v0.26.0"}})
	if err := RunPending(context.Background(), io.Discard, io.Discard); err == nil {
		t.Fatal("a release with no installer must fail")
	}
	if cfg, _ := ReadConfig(); cfg.PinnedVersion != "v0.20.0" {
		t.Errorf("a failed install changed the pin to %q", cfg.PinnedVersion)
	}
}

func TestMoveSelfAsideRestoresAMissingBinary(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "gaia-tui.exe")
	os.WriteFile(exe, []byte("old"), 0o755)
	prev := executable
	executable = func() (string, error) { return exe, nil }
	t.Cleanup(func() { executable = prev })

	restore, err := moveSelfAside()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(exe); !os.IsNotExist(err) {
		t.Fatal("the binary was not moved aside")
	}
	// The installer "succeeded" without writing a new binary.
	if err := restore(); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(exe); string(b) != "old" {
		t.Fatal("the old binary was not put back")
	}

	restore, _ = moveSelfAside()
	os.WriteFile(exe, []byte("new"), 0o755) // the installer wrote one
	restore()
	if b, _ := os.ReadFile(exe); string(b) != "new" {
		t.Fatal("restore overwrote the freshly installed binary")
	}
}
