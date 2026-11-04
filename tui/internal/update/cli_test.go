// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
)

// serveScript serves body as both install scripts and counts the downloads.
func serveScript(t *testing.T, body string) *atomic.Int32 {
	t.Helper()
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		fmt.Fprint(w, body)
	}))
	t.Cleanup(srv.Close)
	prevPS, prevSH := installPS1URL, installSHURL
	installPS1URL, installSHURL = srv.URL+"/install.ps1", srv.URL+"/install.sh"
	t.Cleanup(func() { installPS1URL, installSHURL = prevPS, prevSH })
	return &hits
}

func withCurrent(t *testing.T, v string) {
	t.Helper()
	prev := Current
	Current = v
	t.Cleanup(func() { Current = prev })
}

func TestInstallCLIRunsTheInstallerForThisReleaseInCLIOnlyMode(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("runs install.sh under sh")
	}
	tmp := withTempDir(t)
	withCurrent(t, "0.26.0-rc.2")
	hits := serveScript(t, `echo "tag=$GAIA_RELEASE_TAG cli_only=$GAIA_CLI_ONLY"; echo "to stderr" >&2`)

	var out bytes.Buffer
	if err := InstallCLI(context.Background(), &out); err != nil {
		t.Fatal(err)
	}
	if hits.Load() != 1 {
		t.Errorf("the installer was fetched %d times, want once", hits.Load())
	}
	if !strings.Contains(out.String(), "tag=v0.26.0-rc2 cli_only=1") {
		t.Errorf("the installer did not run pinned to this release in CLI-only mode:\n%s", out.String())
	}
	if !strings.Contains(out.String(), "to stderr") {
		t.Errorf("the installer's errors were not kept:\n%s", out.String())
	}
	assertEmpty(t, tmp)
}

func TestAFailedCLIInstallSaysSo(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("runs install.sh under sh")
	}
	withTempDir(t)
	withCurrent(t, "0.26.0")
	serveScript(t, "exit 3")

	err := InstallCLI(context.Background(), &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "installer stopped") {
		t.Fatalf("err = %v", err)
	}
}

// A dev build has no release to pin, and installing "latest" instead would
// pair this TUI with a CLI it was never tested against.
func TestADevBuildInstallsNoCLI(t *testing.T) {
	withCurrent(t, "dev")
	hits := serveScript(t, "exit 0")

	err := InstallCLI(context.Background(), &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "unreleased build") {
		t.Fatalf("err = %v", err)
	}
	if hits.Load() != 0 {
		t.Error("a dev build downloaded an installer")
	}
}

func TestWindowsHasNoCLIOnlyInstall(t *testing.T) {
	withCurrent(t, "0.26.0")
	prev := goos
	goos = "windows"
	t.Cleanup(func() { goos = prev })
	hits := serveScript(t, "exit 0")

	if CanInstallCLI() {
		t.Error("Windows claims a CLI-only install")
	}
	if err := InstallCLI(context.Background(), &bytes.Buffer{}); err == nil {
		t.Fatal("InstallCLI ran on Windows")
	}
	if hits.Load() != 0 {
		t.Error("Windows downloaded an installer it cannot run CLI-only")
	}
}
