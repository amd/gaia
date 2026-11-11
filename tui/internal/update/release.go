// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
)

// httpClient bounds every request: a black-holed connection must fail, not hang.
var httpClient = &http.Client{Timeout: 20 * time.Second}

// ReleasesPage is where a user can always pick a release by hand. Its feed and
// download routes are also how updates are found: unlike api.github.com, they
// do not spend the 60 anonymous calls an hour one network shares.
const ReleasesPage = "https://github.com/amd/gaia/releases"

const (
	// maxFetch bounds a feed or checksum file read into memory.
	maxFetch = 16 << 20
	// sumsAtOnce keeps a check quick without opening 20 connections to GitHub.
	sumsAtOnce = 8
)

// Release is one published GAIA release.
type Release struct {
	Tag        string
	Prerelease bool
	Updated    string // RFC 3339, from the feed; "" for a tag older than the feed
	Assets     []Asset
}

// Asset is one file attached to a release.
type Asset struct {
	Name string
	URL  string
}

// RemoteError is a failure on GitHub's side or on the way to it, as opposed to
// a broken local setting.
type RemoteError struct{ Err error }

func (e RemoteError) Error() string { return e.Err.Error() }
func (e RemoteError) Unwrap() error { return e.Err }

// Date is the day the release last changed, or "" when the feed did not say.
func (r Release) Date() string {
	t, err := time.Parse(time.RFC3339, r.Updated)
	if err != nil {
		return ""
	}
	return t.Format("2006-01-02")
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

// ListReleases returns the releases in GitHub's feed plus the newest final,
// which ten release candidates can push out of the feed, newest first. Only
// releases newer than current get their assets, as only those can be offered;
// one whose checksum files are not up yet has none.
func ListReleases(ctx context.Context, current string) ([]Release, error) {
	var (
		latest    string
		latestErr error
		wg        sync.WaitGroup
	)
	wg.Add(1)
	go func() {
		defer wg.Done()
		latest, latestErr = latestFinalTag(ctx)
	}()
	feed, feedErr := readFeed(ctx)
	wg.Wait()
	if feedErr != nil {
		return nil, feedErr
	}
	if latestErr != nil {
		return nil, latestErr
	}
	if !slices.ContainsFunc(feed, func(r Release) bool { return r.Tag == latest }) {
		feed = append(feed, newRelease(latest))
	}
	sortNewestFirst(feed)
	newer := slices.IndexFunc(feed, func(r Release) bool { return !Newer(r.Tag, current) })
	if newer < 0 {
		newer = len(feed)
	}
	if err := attachAssets(ctx, feed[:newer]); err != nil {
		return nil, err
	}
	return feed, nil
}

// GetRelease returns the release tagged tag, however old.
func GetRelease(ctx context.Context, tag string) (Release, error) {
	if !ValidTag(tag) {
		return Release{}, fmt.Errorf("%q is not a GAIA release tag — use the form v0.25.0 or v0.25.1-rc2", tag)
	}
	rs := []Release{newRelease(tag)}
	if err := attachAssets(ctx, rs); err != nil {
		return Release{}, err
	}
	if len(rs[0].Assets) == 0 {
		return Release{}, fmt.Errorf("GitHub has no such release as GAIA %s, or it publishes no checksums to verify it with — see %s for the published ones", tag, ReleasesPage)
	}
	return rs[0], nil
}

func newRelease(tag string) Release {
	v, _ := parseVersion(tag)
	return Release{Tag: tag, Prerelease: v.rc != 0}
}

// entryIDRE is the id GitHub gives a feed entry; the rest is the tag.
var entryIDRE = regexp.MustCompile(`^tag:github\.com,2008:Repository/[0-9]+/(.+)$`)

func readFeed(ctx context.Context) ([]Release, error) {
	data, err := fetch(ctx, http.MethodGet, ReleasesPage+".atom", "list GAIA releases")
	if errors.Is(err, errNotPublished) {
		return nil, badStatus("list GAIA releases", "404 Not Found")
	}
	if err != nil {
		return nil, err
	}
	return parseFeed(data)
}

// parseFeed keeps GAIA's own tags; agent packages share the repository's feed.
func parseFeed(data []byte) ([]Release, error) {
	var feed struct {
		XMLName xml.Name `xml:"http://www.w3.org/2005/Atom feed"`
		Entries []struct {
			ID      string `xml:"id"`
			Updated string `xml:"updated"`
		} `xml:"entry"`
	}
	if err := xml.Unmarshal(data, &feed); err != nil {
		return nil, fmt.Errorf("GitHub's release feed cannot be read (%v) — a network sign-in page can cause this; check your connection, or pick a release at %s", err, ReleasesPage)
	}
	rs := make([]Release, 0, len(feed.Entries))
	for _, e := range feed.Entries {
		m := entryIDRE.FindStringSubmatch(e.ID)
		if m == nil {
			return nil, fmt.Errorf("GitHub's release feed has an entry GAIA cannot read (%q) — pick a release at %s", e.ID, ReleasesPage)
		}
		if ValidTag(m[1]) {
			r := newRelease(m[1])
			r.Updated = e.Updated
			rs = append(rs, r)
		}
	}
	return rs, nil
}

// latestFinalTag reads the newest final from where latest/download redirects.
func latestFinalTag(ctx context.Context) (string, error) {
	const what = "find the newest GAIA release"
	noRedirect := *httpClient
	noRedirect.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, ReleasesPage+"/latest/download/"+tuiSums, nil)
	if err != nil {
		return "", err
	}
	resp, err := noRedirect.Do(req)
	if err != nil {
		return "", unreachable(what, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusFound {
		return "", badStatus(what, resp.Status)
	}
	where := resp.Header.Get("Location")
	_, rest, ok := strings.Cut(where, "/releases/download/")
	tag, _, _ := strings.Cut(rest, "/")
	if !ok || !ValidTag(tag) {
		return "", fmt.Errorf("GitHub's newest release points at %q, which is not a GAIA release — pick one at %s", where, ReleasesPage)
	}
	return tag, nil
}

// attachAssets fills in each release's files from its checksum files, which name
// everything worth installing. A 404 leaves that file's assets out: older releases
// have only one of the two, and a new one may still be uploading.
func attachAssets(ctx context.Context, rs []Release) error {
	sumsFiles := []string{tuiSums, installerSums}
	assets := make([][]Asset, len(rs)*len(sumsFiles))
	errs := make([]error, len(assets))
	slots := make(chan struct{}, sumsAtOnce)
	var wg sync.WaitGroup
	for i := range rs {
		for j, file := range sumsFiles {
			wg.Add(1)
			go func(n int, tag, file string) {
				defer wg.Done()
				slots <- struct{}{}
				defer func() { <-slots }()
				assets[n], errs[n] = releaseFiles(ctx, tag, file)
			}(i*len(sumsFiles)+j, rs[i].Tag, file)
		}
	}
	wg.Wait()
	if err := errors.Join(errs...); err != nil {
		return err
	}
	for i := range rs {
		for j := range sumsFiles {
			rs[i].Assets = append(rs[i].Assets, assets[i*len(sumsFiles)+j]...)
		}
	}
	return nil
}

func releaseFiles(ctx context.Context, tag, sumsFile string) ([]Asset, error) {
	base := ReleasesPage + "/download/" + tag + "/"
	data, err := fetch(ctx, http.MethodGet, base+sumsFile, "read GAIA "+tag+"'s "+sumsFile)
	if errors.Is(err, errNotPublished) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	names, err := parseSums(data)
	if err != nil {
		return nil, fmt.Errorf("GAIA %s's %s cannot be read (%v) — a network sign-in page can cause this; check your connection, or pick a release at %s", tag, sumsFile, err, ReleasesPage)
	}
	assets := []Asset{{Name: sumsFile, URL: base + sumsFile}}
	for _, name := range names {
		assets = append(assets, Asset{Name: name, URL: base + name})
	}
	return assets, nil
}

var (
	digestRE   = regexp.MustCompile(`^[0-9a-fA-F]{64}$`)
	fileNameRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+-]*$`)
)

// parseSums returns the file names in sha256sum output.
func parseSums(data []byte) ([]string, error) {
	var names []string
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		if len(fields) != 2 || !digestRE.MatchString(fields[0]) {
			return nil, fmt.Errorf("not a checksum line: %.60q", line)
		}
		name := strings.TrimPrefix(fields[1], "*")
		if !fileNameRE.MatchString(name) {
			return nil, fmt.Errorf("not a release file name: %.60q", name)
		}
		names = append(names, name)
	}
	if len(names) == 0 {
		return nil, errors.New("it lists no files")
	}
	return names, nil
}

// uploaded reports whether a file the checksums name is actually up: GitHub
// shows a release while its files are still uploading.
func uploaded(ctx context.Context, a Asset) (bool, error) {
	_, err := fetch(ctx, http.MethodHead, a.URL, "check "+a.Name)
	if errors.Is(err, errNotPublished) {
		return false, nil
	}
	return err == nil, err
}

var errNotPublished = errors.New("not published")

func fetch(ctx context.Context, method, url, what string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, method, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, unreachable(what, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("%w: %s", errNotPublished, url)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, badStatus(what, resp.Status)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxFetch))
	if err != nil {
		return nil, unreachable(what, err)
	}
	return data, nil
}

func unreachable(what string, err error) error {
	return fmt.Errorf("cannot reach GitHub to %s (%v) — check your connection, or pick a release at %s", what, err, ReleasesPage)
}

func badStatus(what, status string) error {
	return fmt.Errorf("GitHub answered %s when asked to %s — try again later, or pick a release at %s", status, what, ReleasesPage)
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

// TagFor is the release tag of version: "0.25.4-rc.2" is v0.25.4-rc2.
func TagFor(version string) string {
	return "v" + strings.Replace(strings.TrimPrefix(version, "v"), "-rc.", "-rc", 1)
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
// unless includeRCs is set (the user opted into release candidates) or current
// is itself a release candidate (an RC tester keeps getting RCs).
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
