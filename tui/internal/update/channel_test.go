// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

func TestReleaseCandidatesAreAChannelNotAnAutoInstall(t *testing.T) {
	withHome(t)
	rs := []Release{{Tag: "v0.26.0-rc1", Prerelease: true}, {Tag: "v0.25.0"}}
	cfg, _ := ReadConfig()
	if cfg.Prereleases {
		t.Fatal("release candidates must be off by default")
	}
	// Opting in needs no auto-update: it changes what is offered, not when.
	if err := SetPrereleases(true); err != nil {
		t.Fatal(err)
	}
	cfg, _ = ReadConfig()
	if cfg.AutoUpdate {
		t.Fatal("opting into release candidates turned auto-update on")
	}
	if r, _ := Latest(rs, "0.25.0", cfg.Prereleases); r.Tag != "v0.26.0-rc1" {
		t.Errorf("with RCs on, offered %s", r.Tag)
	}
	if s := (Status{Current: "0.25.0", Config: cfg}).Summary(); !strings.Contains(s, "Auto-update is off") ||
		!strings.Contains(s, "Release candidates are offered") {
		t.Errorf("summary = %q", s)
	}
	if err := SetPrereleases(false); err != nil {
		t.Fatal(err)
	}
	cfg, _ = ReadConfig()
	if r, _ := Latest(rs, "0.25.0", cfg.Prereleases); r.Tag != "v0.25.0" {
		t.Errorf("with RCs off, offered %s", r.Tag)
	}
}

func TestChannelSelection(t *testing.T) {
	// Newest first, as ListReleases returns them.
	rs := []Release{
		{Tag: "v0.25.3-rc1", Prerelease: true},
		{Tag: "v0.25.2"},
		{Tag: "v0.25.2-rc4", Prerelease: true},
		{Tag: "v0.25.1"},
	}
	cases := []struct {
		name, current string
		rcs           bool
		want          string
		available     bool
	}{
		{"final user, finals only", "0.25.1", false, "v0.25.2", true},
		{"final user, opted into RCs", "0.25.1", true, "v0.25.3-rc1", true},
		{"RC tester stays on RCs without opting in", "0.25.2-rc.3", false, "v0.25.3-rc1", true},
		{"up to date on the newest final", "0.25.2", false, "v0.25.2", false},
		{"already on the newest candidate", "0.25.3-rc.1", false, "v0.25.3-rc1", false},
	}
	for _, c := range cases {
		r, ok := Latest(rs, c.current, c.rcs)
		if !ok || r.Tag != c.want {
			t.Errorf("%s: offered %q (%v), want %s", c.name, r.Tag, ok, c.want)
			continue
		}
		if got := Newer(r.Tag, c.current); got != c.available {
			t.Errorf("%s: Newer(%s, %s) = %v, want %v", c.name, r.Tag, c.current, got, c.available)
		}
	}
	// An RC tester whose final has shipped is moved onto it.
	finalOut := []Release{{Tag: "v0.25.2"}, {Tag: "v0.25.2-rc4", Prerelease: true}}
	if r, _ := Latest(finalOut, "0.25.2-rc.4", false); r.Tag != "v0.25.2" || !Newer(r.Tag, "0.25.2-rc.4") {
		t.Errorf("RC tester offered %s once the final shipped", r.Tag)
	}
	// A final user whose list holds only candidates is offered nothing.
	if r, ok := Latest([]Release{{Tag: "v0.26.0-rc1", Prerelease: true}}, "0.25.0", false); ok {
		t.Errorf("offered %s from an RC-only list", r.Tag)
	}
}

func TestCheckUsesTheSavedChannel(t *testing.T) {
	withHome(t)
	prev := Current
	Current = "0.25.1"
	t.Cleanup(func() { Current = prev })
	serveReleases(t, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]Release{{Tag: "v0.25.2-rc3", Prerelease: true}, {Tag: "v0.25.1"}})
	})
	st, err := Check(context.Background())
	if err != nil || st.Available {
		t.Fatalf("finals-only check = %+v, %v; want nothing offered", st, err)
	}
	if err := SetPrereleases(true); err != nil {
		t.Fatal(err)
	}
	st, err = Check(context.Background())
	if err != nil || !st.Available || st.Latest.Tag != "v0.25.2-rc3" {
		t.Fatalf("RC check = %+v, %v; want v0.25.2-rc3", st, err)
	}
}

func TestVersionOrderingIsNumericNotLexical(t *testing.T) {
	cases := []struct {
		tag, current string
		want         bool
	}{
		{"v0.25.1-rc10", "0.25.1-rc.9", true},
		{"v0.25.1-rc9", "0.25.1-rc.10", false},
		{"v0.25.10", "0.25.9", true},
		{"v0.25.2-rc1", "0.25.1", true},   // the next line's candidate is newer than the last final
		{"v0.25.1", "0.25.2-rc.1", false}, // and an older final never replaces it
		{"v1.0.0-rc1", "0.99.99", true},
	}
	for _, c := range cases {
		if got := Newer(c.tag, c.current); got != c.want {
			t.Errorf("Newer(%q, %q) = %v, want %v", c.tag, c.current, got, c.want)
		}
	}
	for _, bad := range []string{"0.25.1-beta.1", "0.25.1-rc.0", "0.25", "0.25.1-rc", ""} {
		if Newer("v9.9.9", bad) {
			t.Errorf("an unparseable current %q was offered an update", bad)
		}
	}
	rs := []Release{{Tag: "v0.25.1-rc9"}, {Tag: "v0.25.1"}, {Tag: "v0.25.1-rc11"}, {Tag: "v0.25.1-rc10"}, {Tag: "v0.25.2-rc3"}}
	sortNewestFirst(rs)
	var tags []string
	for _, r := range rs {
		tags = append(tags, r.Tag)
	}
	if got := strings.Join(tags, ","); got != "v0.25.2-rc3,v0.25.1,v0.25.1-rc11,v0.25.1-rc10,v0.25.1-rc9" {
		t.Errorf("sorted = %s", got)
	}
}
