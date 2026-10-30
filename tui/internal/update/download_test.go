// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// withTempDir points os.MkdirTemp at a fresh folder so a test can see what an
// install left behind.
func withTempDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("TMPDIR", dir)
	t.Setenv("TMP", dir)
	t.Setenv("TEMP", dir)
	return dir
}

func assertEmpty(t *testing.T, dir string) {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		t.Errorf("left behind: %s", e.Name())
	}
}

func sha256Hex(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:])
}

const testDeb = "gaia_0.26.0_amd64.deb"

// debRelease serves a .deb release: sums is the checksum file's body, and the
// installer answers with status and body.
func debRelease(t *testing.T, sums string, status int, body string) Release {
	t.Helper()
	// testDeb is an amd64 package; an arm64 runner would look for another name.
	prevArch := goarch
	goarch = "amd64"
	t.Cleanup(func() { goarch = prevArch })
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/sums":
			io.WriteString(w, sums)
		case "/" + testDeb:
			w.WriteHeader(status)
			io.WriteString(w, body)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)
	return Release{Tag: "v0.26.0", Assets: []Asset{
		{Name: testDeb, URL: srv.URL + "/" + testDeb},
		{Name: installerSums, URL: srv.URL + "/sums"},
	}}
}

func TestAFailedDownloadSaysWhyAndLeavesNothing(t *testing.T) {
	withHome(t)
	tmp := withTempDir(t)
	t.Cleanup(Cancel)
	if err := SetPin("v0.20.0"); err != nil {
		t.Fatal(err)
	}
	r := debRelease(t, sha256Hex("pkg")+"  "+testDeb+"\n", http.StatusNotFound, "gone")
	Schedule(Plan{Tag: "v0.26.0", Kind: KindDeb, Release: r})
	err := RunPending(context.Background(), io.Discard, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "was not installed") ||
		!strings.Contains(err.Error(), "cannot download") || !strings.Contains(err.Error(), "404") {
		t.Fatalf("err = %v; want one naming the failed download", err)
	}
	assertEmpty(t, tmp)
	if cfg, _ := ReadConfig(); cfg.PinnedVersion != "v0.20.0" {
		t.Errorf("a failed download changed the pin to %q", cfg.PinnedVersion)
	}
	if _, ok := Pending(); ok {
		t.Error("a failed install stayed queued")
	}
}

func TestATamperedInstallerIsRefusedAndRemoved(t *testing.T) {
	withHome(t)
	tmp := withTempDir(t)
	t.Cleanup(Cancel)
	r := debRelease(t, sha256Hex("the real package")+"  "+testDeb+"\n", http.StatusOK, "something else")
	Schedule(Plan{Tag: "v0.26.0", Kind: KindDeb, Release: r})
	err := RunPending(context.Background(), io.Discard, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "does not match its published checksum") {
		t.Fatalf("err = %v", err)
	}
	assertEmpty(t, tmp)
}

func TestAnInstallerMissingFromTheChecksumsIsRefused(t *testing.T) {
	withHome(t)
	tmp := withTempDir(t)
	t.Cleanup(Cancel)
	// Same name with a suffix must not match: the lookup is exact.
	r := debRelease(t, sha256Hex("pkg")+"  "+testDeb+".old\n", http.StatusOK, "pkg")
	Schedule(Plan{Tag: "v0.26.0", Kind: KindDeb, Release: r})
	err := RunPending(context.Background(), io.Discard, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "no checksum for "+testDeb) {
		t.Fatalf("err = %v", err)
	}
	assertEmpty(t, tmp)
}

func TestAnUnreachableChecksumFileStopsTheInstall(t *testing.T) {
	withHome(t)
	tmp := withTempDir(t)
	t.Cleanup(Cancel)
	r := debRelease(t, "", http.StatusOK, "pkg")
	r.Assets[1].URL += "-missing"
	Schedule(Plan{Tag: "v0.26.0", Kind: KindDeb, Release: r})
	if err := RunPending(context.Background(), io.Discard, io.Discard); err == nil || !strings.Contains(err.Error(), "cannot download") {
		t.Fatalf("err = %v", err)
	}
	assertEmpty(t, tmp)
}

func TestTheWindowsSetupKeepsOnlyItsVerifiedFile(t *testing.T) {
	withHome(t)
	tmp := withTempDir(t)
	prevStart := startDetached
	var started string
	startDetached = func(path string) error { started = path; return nil }
	t.Cleanup(func() { startDetached = prevStart })

	const setup = "gaia-0.26.0-win-x64-setup.exe"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/sums" {
			io.WriteString(w, sha256Hex("setup")+"  "+setup+"\n")
			return
		}
		io.WriteString(w, "setup")
	}))
	t.Cleanup(srv.Close)
	r := Release{Tag: "v0.26.0", Assets: []Asset{
		{Name: setup, URL: srv.URL + "/" + setup}, {Name: installerSums, URL: srv.URL + "/sums"},
	}}
	plan := Plan{Tag: "v0.26.0", Kind: KindWindowsSetup, Release: r}
	if err := runPackage(context.Background(), plan, io.Discard, io.Discard); err != nil {
		t.Fatal(err)
	}
	if b, err := os.ReadFile(started); err != nil || string(b) != "setup" {
		t.Fatalf("the setup it started (%s) is not the verified file: %q, %v", started, b, err)
	}
	os.RemoveAll(filepath.Dir(started))

	// A setup that cannot be opened is an error, and its folder goes.
	startDetached = func(string) error { return errors.New("blocked by policy") }
	err := runPackage(context.Background(), plan, io.Discard, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "blocked by policy") {
		t.Fatalf("err = %v", err)
	}
	assertEmpty(t, tmp)
}

func TestAFailedScriptDownloadSaysWhy(t *testing.T) {
	withHome(t)
	tmp := withTempDir(t)
	t.Cleanup(Cancel)
	srv := httptest.NewServer(http.NotFoundHandler())
	t.Cleanup(srv.Close)
	prevPS, prevSH := installPS1URL, installSHURL
	installPS1URL, installSHURL = srv.URL+"/install.ps1", srv.URL+"/install.sh"
	t.Cleanup(func() { installPS1URL, installSHURL = prevPS, prevSH })

	Schedule(Plan{Tag: "v0.26.0", Kind: KindScript, Pin: true})
	err := RunPending(context.Background(), io.Discard, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "cannot download") || !strings.Contains(err.Error(), "404") {
		t.Fatalf("err = %v", err)
	}
	assertEmpty(t, tmp)
	if cfg, _ := ReadConfig(); cfg.PinnedVersion != "" {
		t.Errorf("a failed install pinned %q", cfg.PinnedVersion)
	}
}
