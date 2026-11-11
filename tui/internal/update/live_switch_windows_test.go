// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"testing"
	"time"
)

const createNoWindow = 0x08000000

// TestLiveVersionSwitch drives check, pin and the binary swap against real
// GitHub in a scratch home. Set GAIA_UPDATE_LIVE_HOME to that folder to run it.
//
// install.ps1 is not run: it writes the user's PATH to the registry, which no
// HOME override redirects. This does its Terminal UI step (checksum, download,
// verify, move into ~/.gaia/bin) around the updater's own moveSelfAside.
func TestLiveVersionSwitch(t *testing.T) {
	home := os.Getenv("GAIA_UPDATE_LIVE_HOME")
	if home == "" {
		t.Skip("set GAIA_UPDATE_LIVE_HOME to a scratch folder to run this against live GitHub")
	}
	realHome, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	if rel, err := filepath.Rel(realHome, home); err == nil && rel == "." || strings.HasPrefix(rel, ".gaia") {
		t.Fatalf("%s is the real home; refusing to run", home)
	}
	exe := filepath.Join(home, ".gaia", "bin", "gaia-tui.exe")
	if err := os.MkdirAll(filepath.Dir(exe), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	prevExe, prevHome, prevCurrent := executable, homeDir, Current
	executable = func() (string, error) { return exe, nil }
	homeDir = func() (string, error) { return home, nil }
	Current = "0.25.3"
	t.Cleanup(func() { executable, homeDir, Current = prevExe, prevHome, prevCurrent })
	ctx := context.Background()

	if err := SetPrereleases(false); err != nil {
		t.Fatal(err)
	}
	if err := SetPin(""); err != nil {
		t.Fatal(err)
	}

	start := time.Now()
	st, err := Check(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("RC off: %v, available=%v, %d releases", time.Since(start), st.Available, len(st.Releases))
	if st.Available {
		t.Fatalf("RC off offered %s to 0.25.3", st.Latest.Tag)
	}

	if err := SetPrereleases(true); err != nil {
		t.Fatal(err)
	}
	start = time.Now()
	st, err = Check(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("RC on: %v, offered %s", time.Since(start), st.Latest.Tag)
	if !st.Available || !st.Latest.Prerelease {
		t.Fatalf("RC on offered %q", st.Latest.Tag)
	}
	newestRC := st.Latest

	seed, err := PlanTag(ctx, "v0.25.3")
	if err != nil {
		t.Fatal(err)
	}
	swap(t, ctx, seed.Release, exe, 0)

	checked := time.Now()
	if st, err = Check(ctx); err != nil {
		t.Fatal(err)
	}
	plan, err := NewPlan(st.Latest, false)
	if err != nil || st.Latest.Tag != newestRC.Tag || plan.Kind != KindScript {
		t.Fatalf("plan %+v, %v", plan, err)
	}
	swap(t, ctx, plan.Release, exe, time.Since(checked))
	if err := SetPin(""); err != nil {
		t.Fatal(err)
	}

	checked = time.Now()
	pin, err := PlanTag(ctx, "v0.25.2")
	if err != nil || !pin.Pin || pin.Kind != KindScript {
		t.Fatalf("pin %+v, %v", pin, err)
	}
	swap(t, ctx, pin.Release, exe, time.Since(checked))
	if err := SetPin(pin.Tag); err != nil {
		t.Fatal(err)
	}
	if cfg, _ := ReadConfig(); cfg.PinnedVersion != "v0.25.2" {
		t.Errorf("pin = %q", cfg.PinnedVersion)
	}
}

var binaryVersionRE = regexp.MustCompile(`^gaia (\S+) `)

func swap(t *testing.T, ctx context.Context, r Release, exe string, check time.Duration) {
	t.Helper()
	bin, ok := r.Asset(tuiBinary())
	sums, ok2 := r.Asset(tuiSums)
	if !ok || !ok2 {
		t.Fatalf("%s lacks a build or checksums: %+v", r.Tag, r.Assets)
	}
	dir := t.TempDir()
	start := time.Now()
	sumsPath := filepath.Join(dir, tuiSums)
	if err := download(ctx, sums.URL, sumsPath); err != nil {
		t.Fatal(err)
	}
	want, err := checksumFor(sumsPath, bin.Name)
	if err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, bin.Name)
	if err := download(ctx, bin.URL, file); err != nil {
		t.Fatal(err)
	}
	if got, err := sha256File(file); err != nil || got != want {
		t.Fatalf("%s checksum %s, want %s (%v)", bin.Name, got, want, err)
	}
	downloaded := time.Since(start)

	start = time.Now()
	restore := func() error { return nil }
	if _, err := os.Stat(exe); err == nil {
		if restore, err = moveSelfAside(); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Rename(file, exe); err != nil {
		t.Fatal(err)
	}
	if err := restore(); err != nil {
		t.Fatal(err)
	}
	installed := time.Since(start)
	if err := RemoveLeftover(); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(exe, "version")
	cmd.Dir = filepath.Dir(exe)
	cmd.Env = append(os.Environ(), "GAIA_DISABLE_UPDATE=1")
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createNoWindow}
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("%s version: %v", exe, err)
	}
	m := binaryVersionRE.FindSubmatch(out)
	if m == nil || !SameVersion(r.Tag, string(m[1])) {
		t.Fatalf("after installing %s the binary says %q", r.Tag, out)
	}
	t.Log(fmt.Sprintf("switched to %s (%s): check %v, download+verify %v, install %v",
		r.Tag, strings.TrimSpace(string(out)), check.Round(time.Millisecond), downloaded.Round(time.Millisecond), installed.Round(time.Millisecond)))
}
