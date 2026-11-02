// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"strings"
	"testing"
)

func TestCheckSkipsAReleaseWithNothingForThisMachine(t *testing.T) {
	prev := Current
	Current = "0.24.0"
	t.Cleanup(func() { Current = prev })
	// v0.26.0 carries only the desktop app's setup; v0.25.0 shipped no
	// Terminal UI Windows setup at all.
	withGitHub(t, &fakeGitHub{
		feed:   atom("v0.26.0", "v0.25.1", "v0.25.0"),
		latest: "v0.26.0",
		sums: map[string]string{
			"v0.26.0/" + installerSums: sumsOf("gaia-agent-ui-0.26.0-x64-setup.exe"),
			"v0.25.1/" + installerSums: sumsOf("gaia-0.25.1-win-x64-setup.exe"),
			"v0.25.0/" + installerSums: sumsOf("gaia_0.25.0_amd64.deb"),
			"v0.25.0/" + tuiSums:       sumsOf("gaia-windows-amd64.exe"),
		},
	})

	withHome(t)
	withExecutable(t, `C:\Programs\GAIA Terminal Hub\gaia-tui.exe`, "windows")
	st, err := Check(context.Background())
	if err != nil || !st.Available || st.Latest.Tag != "v0.25.1" {
		t.Fatalf("windows setup install offered %q (%v, %v); want v0.25.1", st.Latest.Tag, st.Available, err)
	}
	if len(st.Releases) != 3 {
		t.Errorf("/update list must still show every release, got %d", len(st.Releases))
	}

	// A script install on arm64 Linux needs that build, and none ships it.
	withScriptInstall(t)
	goarch = "arm64"
	st, err = Check(context.Background())
	if err != nil || st.Available || st.Latest.Tag != "" {
		t.Fatalf("arm64 script install offered %q (%v, %v); want nothing", st.Latest.Tag, st.Available, err)
	}
	if st.Unsupported.Tag != "v0.26.0" || !strings.Contains(st.Summary(), "no installer for this machine") {
		t.Errorf("arm64 must hear v0.26.0 is out but not installable here, got %q", st.Summary())
	}

	// A binary no installer put there is offered only a release with a build
	// for this OS and architecture.
	home := withHome(t)
	withExecutable(t, home+"/src/gaia-tui.exe", "windows")
	goarch = "amd64"
	st, err = Check(context.Background())
	if err != nil || st.Latest.Tag != "v0.25.0" {
		t.Fatalf("hand-placed binary offered %q (%v); want v0.25.0", st.Latest.Tag, err)
	}
}

func TestInstallableMatchesThisMachinesArch(t *testing.T) {
	withHome(t)
	sums := Asset{Name: installerSums}
	amd := Release{Tag: "v0.26.0", Assets: []Asset{{Name: "gaia_0.26.0_amd64.deb"}, {Name: "gaia-0.26.0.x86_64.rpm"}, sums}}
	arm := Release{Tag: "v0.26.0", Assets: []Asset{{Name: "gaia_0.26.0_arm64.deb"}, {Name: "gaia-0.26.0.aarch64.rpm"}, sums}}
	prevArch := goarch
	t.Cleanup(func() { goarch = prevArch })
	for _, c := range []struct {
		arch string
		r    Release
		want bool
	}{
		{"amd64", amd, true}, {"amd64", arm, false},
		{"arm64", arm, true}, {"arm64", amd, false},
	} {
		goarch = c.arch
		for _, k := range []Kind{KindDeb, KindRpm} {
			if got := Installable(c.r, k); got != c.want {
				t.Errorf("%s on %s with %v = %v, want %v", k, c.arch, c.r.Assets, got, c.want)
			}
		}
	}
	goarch = "amd64"
	if Installable(Release{Tag: "v0.26.0", Assets: []Asset{{Name: "gaia_0.26.0_amd64.deb"}}}, KindDeb) {
		t.Error("an installer with no checksums was taken as installable")
	}
	if Installable(Release{Tag: "v0.26.0", Assets: []Asset{{Name: "gaia-linux-amd64"}}}, KindScript) {
		t.Error("a script build with no checksums was taken as installable")
	}
}

func TestInstallerNamesRejectPathSeparators(t *testing.T) {
	prevArch := goarch
	t.Cleanup(func() { goarch = prevArch })
	goarch = "amd64"
	bad := map[Kind][]string{
		KindWindowsSetup: {`gaia-0.26.0\..\evil-win-x64-setup.exe`, "gaia-0.26.0/../evil-win-x64-setup.exe"},
		KindMacPkg:       {`gaia-0.26.0\x-darwin-x64.pkg`},
		KindDeb:          {`gaia_0.26.0\x_amd64.deb`, "gaia_0.26.0/x_amd64.deb"},
		KindRpm:          {`gaia-0.26.0\x.x86_64.rpm`},
	}
	for kind, names := range bad {
		re, err := assetPattern(kind)
		if err != nil {
			t.Fatal(err)
		}
		for _, n := range names {
			if re.MatchString(n) {
				t.Errorf("%s pattern accepted %q", kind, n)
			}
		}
	}
	// The real asset names of v0.25.2-rc3 still match.
	good := map[Kind]string{
		KindWindowsSetup: "gaia-0.25.2-rc.3-win-x64-setup.exe",
		KindMacPkg:       "gaia-0.25.2-rc.3-darwin-x64.pkg",
		KindDeb:          "gaia_0.25.2-rc.3_amd64.deb",
		KindRpm:          "gaia-0.25.2-rc.3.x86_64.rpm",
	}
	for kind, n := range good {
		if re, _ := assetPattern(kind); !re.MatchString(n) {
			t.Errorf("%s pattern rejected the real asset %q", kind, n)
		}
	}
}

func TestGoingBackBeforeTheInstallersExistedSaysWhy(t *testing.T) {
	// v0.25.3 shipped the Terminal UI binaries only: no setup, .pkg, .deb or .rpm.
	withGitHub(t, &fakeGitHub{
		feed:   atom("v0.25.4", "v0.25.3"),
		latest: "v0.25.4",
		sums: map[string]string{
			"v0.25.4/" + installerSums: sumsOf("gaia-0.25.4-win-x64-setup.exe"),
			"v0.25.3/" + tuiSums:       sumsOf("gaia-windows-amd64.exe"),
		},
	})
	withHome(t)
	withExecutable(t, `C:\Programs\GAIA Terminal Hub\gaia-tui.exe`, "windows")

	_, err := PlanTag(context.Background(), "v0.25.3")
	if err == nil || !strings.Contains(err.Error(), "came out before GAIA had a Windows setup installer") {
		t.Fatalf("going back to v0.25.3 from a Windows setup install said %v; want why it can't", err)
	}
}
