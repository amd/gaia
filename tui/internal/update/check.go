// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"fmt"
	"slices"
	"strings"
)

// Current is the running build's version, set from the CLI's ldflags value.
// "dev" (an unreleased build) is never offered an update.
var Current = "dev"

// Status is where updates stand for this install.
type Status struct {
	Current   string
	Config    Config
	Latest    Release // the newest installable release; zero when nothing newer is
	Available bool    // Latest is set
	// Unsupported is a newer release on this channel that ships nothing this
	// machine can install (Windows arm64, an arm64 .deb); zero otherwise.
	Unsupported Release
	Releases    []Release
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
	rs, err := ListReleases(ctx, Current)
	if err != nil {
		return st, RemoteError{err}
	}
	st.Releases = rs
	// Offer only a release this machine can install; NewPlan says why when
	// the install itself can't replace this binary.
	kind, _ := DetectKind()
	var installable, published []Release
	for _, r := range rs {
		if len(r.Assets) > 0 {
			published = append(published, r)
		}
		if Installable(r, kind) {
			installable = append(installable, r)
		}
	}
	for {
		latest, ok := Latest(installable, Current, cfg.Prereleases)
		if !ok {
			break
		}
		file, err := installFile(latest, kind)
		if err != nil {
			return st, err
		}
		up, err := uploaded(ctx, file)
		if err != nil {
			return st, RemoteError{err}
		}
		if up {
			st.Latest, st.Available = latest, true
			break
		}
		// Still publishing: neither on offer nor "no installer for this machine".
		installable = without(installable, latest.Tag)
		published = without(published, latest.Tag)
	}
	if newest, ok := Latest(published, Current, cfg.Prereleases); ok && Newer(newest.Tag, Current) &&
		(!st.Available || Newer(newest.Tag, st.Latest.Tag)) {
		st.Unsupported = newest
	}
	return st, nil
}

func without(rs []Release, tag string) []Release {
	return slices.DeleteFunc(slices.Clone(rs), func(r Release) bool { return r.Tag == tag })
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
	case s.Unsupported.Tag != "":
		fmt.Fprintf(&b, " GAIA %s is out, but it has no installer for this machine — see %s.",
			s.Unsupported.Tag, ReleasesPage)
	default:
		b.WriteString(" It is up to date.")
	}
	if s.Config.AutoUpdate {
		b.WriteString(" Auto-update is on: a newer release installs when you quit.")
	} else {
		b.WriteString(" Auto-update is off.")
	}
	if s.Config.Prereleases {
		b.WriteString(" Release candidates are offered too.")
	} else if c, ok := parseVersion(s.Current); ok && c.rc != 0 {
		b.WriteString(" Release candidates are offered because this is one.")
	}
	return b.String()
}
