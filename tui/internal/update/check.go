// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"fmt"
	"strings"
)

// Current is the running build's version, set from the CLI's ldflags value.
// "dev" (an unreleased build) is never offered an update.
var Current = "dev"

// Status is where updates stand for this install.
type Status struct {
	Current   string
	Config    Config
	Latest    Release // zero when GitHub listed no release for this channel
	Available bool    // Latest is newer than Current
	Releases  []Release
}

// IsDevBuild reports whether the running binary is an unreleased build.
func IsDevBuild() bool {
	_, ok := parseVersion(Current)
	return !ok
}

// Check reads the preferences and asks GitHub what is newest.
func Check(ctx context.Context) (Status, error) {
	cfg, err := ReadConfig()
	if err != nil {
		return Status{Current: Current}, err
	}
	st := Status{Current: Current, Config: cfg}
	if IsDevBuild() {
		return st, nil
	}
	rs, err := ListReleases(ctx)
	if err != nil {
		return st, err
	}
	st.Releases = rs
	if latest, ok := Latest(rs, Current); ok {
		st.Latest = latest
		st.Available = Newer(latest.Tag, Current)
	}
	return st, nil
}

// Summary is the one status line /update prints.
func (s Status) Summary() string {
	var b strings.Builder
	fmt.Fprintf(&b, "Running GAIA %s.", s.Current)
	switch {
	case IsDevBuild():
		b.WriteString(" This is a development build, so it is not offered updates.")
	case s.Available && s.Config.PinnedVersion != "":
		fmt.Fprintf(&b, " GAIA %s is out, but you pinned %s, so updates are paused.", s.Latest.Tag, s.Config.PinnedVersion)
	case s.Available:
		fmt.Fprintf(&b, " GAIA %s is available.", s.Latest.Tag)
	default:
		b.WriteString(" It is up to date.")
	}
	if s.Config.AutoUpdate {
		b.WriteString(" Auto-update is on: a newer release installs when you quit.")
	} else {
		b.WriteString(" Auto-update is off.")
	}
	return b.String()
}
