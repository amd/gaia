// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// apiBase is GitHub's API root for the GAIA repository; swapped by tests.
var apiBase = "https://api.github.com/repos/amd/gaia"

// httpClient bounds every request: a black-holed connection must fail, not hang.
var httpClient = &http.Client{Timeout: 20 * time.Second}

// ReleasesPage is where a user can always pick a release by hand.
const ReleasesPage = "https://github.com/amd/gaia/releases"

// Release is one published GAIA release.
type Release struct {
	Tag        string  `json:"tag_name"`
	Prerelease bool    `json:"prerelease"`
	Draft      bool    `json:"draft"`
	Published  string  `json:"published_at"`
	Assets     []Asset `json:"assets"`
}

// Asset is one file attached to a release.
type Asset struct {
	Name string `json:"name"`
	URL  string `json:"browser_download_url"`
}

// Asset returns the attached file called name.
func (r Release) Asset(name string) (Asset, bool) {
	for _, a := range r.Assets {
		if a.Name == name {
			return a, true
		}
	}
	return Asset{}, false
}

// tagRE is the tag form the install scripts accept: v0.25.0 or v0.25.1-rc2.
var tagRE = regexp.MustCompile(`^v[0-9]+\.[0-9]+\.[0-9]+(-rc[1-9][0-9]*)?$`)

// ValidTag reports whether tag names a GAIA release.
func ValidTag(tag string) bool { return tagRE.MatchString(tag) }

// Disabled reports whether this install opted out of update checks — the
// same GAIA_DISABLE_UPDATE=1 the Agent UI honours.
func Disabled() bool { return os.Getenv("GAIA_DISABLE_UPDATE") == "1" }

// ListReleases returns the 30 most recent published releases, newest first,
// drafts dropped.
func ListReleases(ctx context.Context) ([]Release, error) {
	var all []Release
	if err := getJSON(ctx, apiBase+"/releases?per_page=30", "list GAIA releases", &all); err != nil {
		return nil, err
	}
	out := all[:0]
	for _, r := range all {
		if !r.Draft && ValidTag(r.Tag) {
			out = append(out, r)
		}
	}
	sortNewestFirst(out)
	return out, nil
}

// GetRelease returns the published release tagged tag, however old.
func GetRelease(ctx context.Context, tag string) (Release, error) {
	if !ValidTag(tag) {
		return Release{}, fmt.Errorf("%q is not a GAIA release tag — use the form v0.25.0 or v0.25.1-rc2", tag)
	}
	var r Release
	if err := getJSON(ctx, apiBase+"/releases/tags/"+tag, "look up GAIA "+tag, &r); err != nil {
		return Release{}, err
	}
	if r.Draft {
		return Release{}, fmt.Errorf("GAIA %s is not published yet — pick a release at %s", tag, ReleasesPage)
	}
	return r, nil
}

func getJSON(ctx context.Context, url, what string, into any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	// Anonymous requests share 60 an hour per network; a token the user set
	// lifts that.
	if tok := githubToken(); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("cannot reach GitHub to %s (%v) — check your connection, or pick a release at %s", what, err, ReleasesPage)
	}
	defer resp.Body.Close()
	if (resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusTooManyRequests) &&
		resp.Header.Get("X-RateLimit-Remaining") == "0" {
		return fmt.Errorf("GitHub's hourly limit for this network is used up, so GAIA could not %s%s — "+
			"try again then, or set GH_TOKEN to a GitHub token to lift the limit", what, rateLimitReset(resp.Header.Get("X-RateLimit-Reset")))
	}
	if resp.StatusCode == http.StatusNotFound {
		return fmt.Errorf("GitHub has no such release (%s) — see %s for the published ones", what, ReleasesPage)
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GitHub answered %s when asked to %s — try again later, or pick a release at %s", resp.Status, what, ReleasesPage)
	}
	if err := json.NewDecoder(resp.Body).Decode(into); err != nil {
		return fmt.Errorf("cannot read GitHub's answer to %s: %w", what, err)
	}
	return nil
}

func githubToken() string {
	if tok := strings.TrimSpace(os.Getenv("GH_TOKEN")); tok != "" {
		return tok
	}
	return strings.TrimSpace(os.Getenv("GITHUB_TOKEN"))
}

// rateLimitReset turns X-RateLimit-Reset (unix seconds) into " (it resets at 15:04)".
func rateLimitReset(v string) string {
	secs, err := strconv.ParseInt(v, 10, 64)
	if err != nil {
		return ""
	}
	return " (it resets at " + time.Unix(secs, 0).Format("15:04") + ")"
}

// version is a parsed X.Y.Z with an optional release-candidate number; rc 0
// is the final, which sorts after every candidate of the same X.Y.Z.
type version struct {
	parts [3]int
	rc    int
}

// parseVersion reads a tag ("v0.25.1-rc4") or a binary's version string
// ("0.25.1-rc.4", "0.25.0").
func parseVersion(s string) (version, bool) {
	s = strings.TrimPrefix(strings.TrimSpace(s), "v")
	core, pre, hasPre := strings.Cut(s, "-")
	fields := strings.Split(core, ".")
	if len(fields) != 3 {
		return version{}, false
	}
	var v version
	for i, f := range fields {
		n, err := strconv.Atoi(f)
		if err != nil || n < 0 {
			return version{}, false
		}
		v.parts[i] = n
	}
	if hasPre {
		n, err := strconv.Atoi(strings.TrimPrefix(strings.TrimPrefix(pre, "rc"), "."))
		if !strings.HasPrefix(pre, "rc") || err != nil || n < 1 {
			return version{}, false
		}
		v.rc = n
	}
	return v, true
}

// less reports a < b.
func (a version) less(b version) bool {
	for i := range a.parts {
		if a.parts[i] != b.parts[i] {
			return a.parts[i] < b.parts[i]
		}
	}
	if a.rc == b.rc {
		return false
	}
	if a.rc == 0 || b.rc == 0 {
		return a.rc != 0 // a candidate sorts before its final
	}
	return a.rc < b.rc
}

// Newer reports whether tag is a later release than current. A current that
// cannot be parsed (a dev build) is never behind.
func Newer(tag, current string) bool {
	t, ok1 := parseVersion(tag)
	c, ok2 := parseVersion(current)
	return ok1 && ok2 && c.less(t)
}

// SameVersion reports whether tag is the release current was built from.
func SameVersion(tag, current string) bool {
	t, ok1 := parseVersion(tag)
	c, ok2 := parseVersion(current)
	return ok1 && ok2 && t == c
}

func sortNewestFirst(rs []Release) {
	for i := 1; i < len(rs); i++ {
		for j := i; j > 0; j-- {
			a, _ := parseVersion(rs[j-1].Tag)
			b, _ := parseVersion(rs[j].Tag)
			if !a.less(b) {
				break
			}
			rs[j-1], rs[j] = rs[j], rs[j-1]
		}
	}
}

// Latest is the newest release this install should be offered: finals only,
// unless includeRCs is set or current is itself a release candidate (an RC
// tester keeps getting RCs).
func Latest(rs []Release, current string, includeRCs bool) (Release, bool) {
	c, _ := parseVersion(current)
	for _, r := range rs {
		if r.Prerelease && c.rc == 0 && !includeRCs {
			continue
		}
		return r, true
	}
	return Release{}, false
}
