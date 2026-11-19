// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	testRepoID = "903960898"
	page       = "https://github.com/amd/gaia/releases"
)

// fakeGitHub stands in for the network at the transport, so a request to any
// host is seen, including one this package no longer means to make.
type fakeGitHub struct {
	feed      string
	feedCode  int
	latest    string
	sums      map[string]string // "<tag>/<file>" → body
	sumsCode  map[string]int    // "<tag>/<file>" → a status other than 200/404
	notUp     map[string]bool   // "<tag>/<file>" whose HEAD answers 404
	delay     time.Duration
	mu        sync.Mutex
	requests  []string
	inFlight  int
	mostAtOne int
}

func (f *fakeGitHub) RoundTrip(req *http.Request) (*http.Response, error) {
	f.mu.Lock()
	f.requests = append(f.requests, req.Method+" "+req.URL.String())
	f.inFlight++
	f.mostAtOne = max(f.mostAtOne, f.inFlight)
	f.mu.Unlock()
	defer func() {
		f.mu.Lock()
		f.inFlight--
		f.mu.Unlock()
	}()
	if f.delay > 0 {
		time.Sleep(f.delay)
	}
	reply := func(code int, body string, headers ...string) (*http.Response, error) {
		h := http.Header{}
		for i := 0; i+1 < len(headers); i += 2 {
			h.Set(headers[i], headers[i+1])
		}
		return &http.Response{
			StatusCode: code, Status: fmt.Sprintf("%d %s", code, http.StatusText(code)),
			Header: h, Body: io.NopCloser(strings.NewReader(body)), Request: req,
		}, nil
	}
	url := req.URL.String()
	switch {
	case req.URL.Host == "api.github.com":
		return reply(http.StatusForbidden, "rate limit exceeded", "X-RateLimit-Remaining", "0")
	case url == page+".atom":
		if f.feedCode != 0 {
			return reply(f.feedCode, "")
		}
		return reply(http.StatusOK, f.feed)
	case url == page+"/latest/download/"+tuiSums:
		return reply(http.StatusFound, "", "Location", page+"/download/"+f.latest+"/"+tuiSums)
	case strings.HasPrefix(url, page+"/download/"):
		key := strings.TrimPrefix(url, page+"/download/")
		if code, ok := f.sumsCode[key]; ok {
			return reply(code, "")
		}
		if body, ok := f.sums[key]; ok {
			return reply(http.StatusOK, body)
		}
		if req.Method == http.MethodHead && !f.notUp[key] {
			return reply(http.StatusOK, "")
		}
		return reply(http.StatusNotFound, "Not Found")
	}
	return reply(http.StatusNotFound, "Not Found")
}

func (f *fakeGitHub) seen() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Sorted(slices.Values(f.requests))
}

func withGitHub(t *testing.T, f *fakeGitHub) {
	t.Helper()
	prev := httpClient
	httpClient = &http.Client{Transport: f}
	t.Cleanup(func() { httpClient = prev })
}

func atom(tags ...string) string {
	var b strings.Builder
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-US">
  <id>tag:github.com,2008:https://github.com/amd/gaia/releases</id>
  <title>Release notes from gaia</title>`)
	for _, tag := range tags {
		fmt.Fprintf(&b, `
  <entry>
    <id>tag:github.com,2008:Repository/%s/%s</id>
    <updated>2026-11-01T22:05:25Z</updated>
    <title>%s</title>
    <content type="html">&lt;p&gt;notes&lt;/p&gt;</content>
  </entry>`, testRepoID, tag, tag)
	}
	b.WriteString("\n</feed>\n")
	return b.String()
}

func sumsOf(names ...string) string {
	var b strings.Builder
	for _, n := range names {
		b.WriteString(strings.Repeat("a", 64) + "  " + n + "\n")
	}
	return b.String()
}

// tuiFiles publishes tag's script-install files.
func tuiFiles(tag string) map[string]string {
	return map[string]string{tag + "/" + tuiSums: sumsOf("gaia-linux-amd64", "gaia-windows-amd64.exe")}
}

func merged(ms ...map[string]string) map[string]string {
	out := map[string]string{}
	for _, m := range ms {
		for k, v := range m {
			out[k] = v
		}
	}
	return out
}

func onVersion(t *testing.T, v string) {
	t.Helper()
	prev := Current
	Current = v
	t.Cleanup(func() { Current = prev })
}

func noAPICalls(t *testing.T, f *fakeGitHub) {
	t.Helper()
	for _, r := range f.seen() {
		if strings.Contains(r, "api.github.com") {
			t.Errorf("the rate-limited API was consulted: %s", r)
		}
	}
}

func TestCheckNeverConsultsTheRateLimitedAPI(t *testing.T) {
	withScriptInstall(t)
	onVersion(t, "0.25.1")
	f := &fakeGitHub{
		feed:   atom("v0.25.2", "v0.25.1"),
		latest: "v0.25.2",
		sums:   merged(tuiFiles("v0.25.2"), tuiFiles("v0.25.1")),
	}
	withGitHub(t, f)
	st, err := Check(context.Background())
	if err != nil || !st.Available || st.Latest.Tag != "v0.25.2" {
		t.Fatalf("check = %+v, %v; want v0.25.2 offered", st, err)
	}
	noAPICalls(t, f)
	want := []string{
		"GET " + page + "/download/v0.25.2/" + installerSums,
		"GET " + page + "/download/v0.25.2/" + tuiSums,
		"GET " + page + ".atom",
		"HEAD " + page + "/download/v0.25.2/gaia-linux-amd64",
		"HEAD " + page + "/latest/download/" + tuiSums,
	}
	slices.Sort(want)
	if got := f.seen(); !slices.Equal(got, want) {
		t.Errorf("requests:\n%s\nwant:\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	if a, ok := st.Latest.Asset("gaia-linux-amd64"); !ok || a.URL != page+"/download/v0.25.2/gaia-linux-amd64" {
		t.Errorf("asset = %+v, %v", a, ok)
	}
}

func TestPinningAnOldTagNeverConsultsTheAPIOrTheFeed(t *testing.T) {
	withScriptInstall(t)
	f := &fakeGitHub{sums: tuiFiles("v0.20.0")}
	withGitHub(t, f)
	p, err := PlanTag(context.Background(), "v0.20.0")
	if err != nil || !p.Pin || p.Kind != KindScript {
		t.Fatalf("plan = %+v, %v", p, err)
	}
	want := []string{
		"GET " + page + "/download/v0.20.0/" + installerSums,
		"GET " + page + "/download/v0.20.0/" + tuiSums,
		"HEAD " + page + "/download/v0.20.0/gaia-linux-amd64",
	}
	if got := f.seen(); !slices.Equal(got, want) {
		t.Errorf("requests = %q, want %q", got, want)
	}
}

func TestPinningARefusesATagWhoseFileIsNotUpYet(t *testing.T) {
	withScriptInstall(t)
	f := &fakeGitHub{sums: tuiFiles("v0.25.2"), notUp: map[string]bool{"v0.25.2/gaia-linux-amd64": true}}
	withGitHub(t, f)
	if _, err := PlanTag(context.Background(), "v0.25.2"); err == nil || !strings.Contains(err.Error(), "still publishing") {
		t.Fatalf("err = %v", err)
	}
}

func TestTenCandidatesDoNotHideTheFinal(t *testing.T) {
	withScriptInstall(t)
	onVersion(t, "0.25.2")
	var tags []string
	sums := tuiFiles("v0.25.3")
	for rc := 10; rc >= 1; rc-- {
		tag := fmt.Sprintf("v0.25.4-rc%d", rc)
		tags = append(tags, tag)
		sums = merged(sums, tuiFiles(tag))
	}
	f := &fakeGitHub{feed: atom(tags...), latest: "v0.25.3", sums: sums}
	withGitHub(t, f)
	st, err := Check(context.Background())
	if err != nil || !st.Available || st.Latest.Tag != "v0.25.3" {
		t.Fatalf("offered %q (%v, %v); want the final outside the feed, v0.25.3", st.Latest.Tag, st.Available, err)
	}
	noAPICalls(t, f)
	if !slices.Contains(f.seen(), "HEAD "+page+"/latest/download/"+tuiSums) {
		t.Errorf("the latest route was not read: %q", f.seen())
	}
	if !slices.Contains(f.seen(), "GET "+page+"/download/v0.25.3/"+tuiSums) {
		t.Errorf("the final's checksums were not read: %q", f.seen())
	}
	if r := st.Releases[len(st.Releases)-1]; r.Tag != "v0.25.3" || r.Prerelease {
		t.Errorf("oldest listed = %+v", r)
	}
}

func TestPrereleaseComesFromTheTag(t *testing.T) {
	withScriptInstall(t)
	f := &fakeGitHub{feed: atom("v0.25.4-rc3", "v0.25.3"), latest: "v0.25.3", sums: merged(tuiFiles("v0.25.4-rc3"), tuiFiles("v0.25.3"))}
	withGitHub(t, f)
	rs, err := ListReleases(context.Background(), "0.25.0")
	if err != nil {
		t.Fatal(err)
	}
	if len(rs) != 2 || !rs[0].Prerelease || rs[1].Prerelease {
		t.Fatalf("releases = %+v", rs)
	}
	if rs[0].Date() != "2026-11-01" {
		t.Errorf("date = %q", rs[0].Date())
	}
}

func TestANewerReleaseMissingItsSumsIsSkippedNotAnError(t *testing.T) {
	withScriptInstall(t)
	onVersion(t, "0.25.1")
	if err := SetPrereleases(true); err != nil {
		t.Fatal(err)
	}
	f := &fakeGitHub{feed: atom("v0.25.3-rc1", "v0.25.2"), latest: "v0.25.2", sums: tuiFiles("v0.25.2")}
	withGitHub(t, f)
	st, err := Check(context.Background())
	if err != nil || !st.Available || st.Latest.Tag != "v0.25.2" {
		t.Fatalf("offered %q (%v, %v); want v0.25.2", st.Latest.Tag, st.Available, err)
	}
	if st.Unsupported.Tag != "" {
		t.Errorf("a release still publishing was called unsupported: %s", st.Unsupported.Tag)
	}
	if len(st.Releases) != 2 {
		t.Errorf("/update list must still name it, got %d", len(st.Releases))
	}
	for _, r := range f.seen() {
		if strings.HasPrefix(r, "HEAD") && strings.Contains(r, "v0.25.3-rc1") {
			t.Errorf("probed a release with no checksums: %s", r)
		}
	}
}

func TestAReleaseWhoseBinaryIsNotUpIsSkippedToTheNext(t *testing.T) {
	withScriptInstall(t)
	onVersion(t, "0.25.1")
	f := &fakeGitHub{
		feed:   atom("v0.25.3", "v0.25.2"),
		latest: "v0.25.3",
		sums:   merged(tuiFiles("v0.25.3"), tuiFiles("v0.25.2")),
		notUp:  map[string]bool{"v0.25.3/gaia-linux-amd64": true},
	}
	withGitHub(t, f)
	st, err := Check(context.Background())
	if err != nil || !st.Available || st.Latest.Tag != "v0.25.2" {
		t.Fatalf("offered %q (%v, %v); want v0.25.2", st.Latest.Tag, st.Available, err)
	}
	if st.Unsupported.Tag != "" {
		t.Errorf("a release still uploading was called unsupported: %s", st.Unsupported.Tag)
	}
	for _, want := range []string{"HEAD " + page + "/download/v0.25.3/gaia-linux-amd64", "HEAD " + page + "/download/v0.25.2/gaia-linux-amd64"} {
		if !slices.Contains(f.seen(), want) {
			t.Errorf("missing %s in %q", want, f.seen())
		}
	}
}

func TestASumsFileFailingForAnotherReasonIsAnError(t *testing.T) {
	withScriptInstall(t)
	onVersion(t, "0.25.1")
	f := &fakeGitHub{
		feed:     atom("v0.25.2", "v0.25.1"),
		latest:   "v0.25.1",
		sums:     tuiFiles("v0.25.1"),
		sumsCode: map[string]int{"v0.25.2/" + tuiSums: http.StatusBadGateway},
	}
	withGitHub(t, f)
	_, err := Check(context.Background())
	var remote RemoteError
	if !errors.As(err, &remote) || !strings.Contains(err.Error(), "502") {
		t.Fatalf("err = %v; a 502 must not read as an unpublished release", err)
	}
}

func TestGarbledFeedIsARemoteErrorNamingTheFeed(t *testing.T) {
	for name, body := range map[string]string{
		"sign-in page": "<!DOCTYPE html><html><body><p>Sign in to Wi-Fi<br></body></html>",
		"another xml":  `<?xml version="1.0"?><rss><channel/></rss>`,
		"truncated":    atom("v0.25.3")[:300],
		"not xml":      "}{",
		"new id shape": strings.ReplaceAll(atom("v0.25.3"), "Repository/"+testRepoID+"/", "Release:"),
	} {
		t.Run(name, func(t *testing.T) {
			withScriptInstall(t)
			onVersion(t, "0.25.1")
			f := &fakeGitHub{feed: body, latest: "v0.25.3", sums: tuiFiles("v0.25.3")}
			withGitHub(t, f)
			_, err := Check(context.Background())
			var remote RemoteError
			if !errors.As(err, &remote) || !strings.Contains(err.Error(), "feed") || !strings.Contains(err.Error(), ReleasesPage) {
				t.Fatalf("err = %v", err)
			}
			noAPICalls(t, f)
		})
	}
}

func TestGarbledSumsIsAnErrorNamingTheFile(t *testing.T) {
	for name, body := range map[string]string{
		"sign-in page": "<html>Sign in</html>",
		"empty":        "\n",
		"path in name": strings.Repeat("a", 64) + "  ../evil.exe\n",
	} {
		t.Run(name, func(t *testing.T) {
			withScriptInstall(t)
			f := &fakeGitHub{sums: map[string]string{"v0.25.2/" + tuiSums: body}}
			withGitHub(t, f)
			_, err := GetRelease(context.Background(), "v0.25.2")
			if err == nil || !strings.Contains(err.Error(), tuiSums) {
				t.Fatalf("err = %v", err)
			}
		})
	}
}

func TestANonGAIALatestIsAnError(t *testing.T) {
	withScriptInstall(t)
	f := &fakeGitHub{feed: atom("v0.25.2"), latest: "agent-pkg-gaia-v0.3.3", sums: tuiFiles("v0.25.2")}
	withGitHub(t, f)
	if _, err := ListReleases(context.Background(), "0.25.0"); err == nil || !strings.Contains(err.Error(), "agent-pkg") {
		t.Fatalf("err = %v", err)
	}
}

func TestOnlyReleasesThatCouldBeOfferedCostARequest(t *testing.T) {
	withScriptInstall(t)
	onVersion(t, "0.25.3")
	f := &fakeGitHub{feed: atom("v0.25.3", "v0.25.2", "v0.25.1"), latest: "v0.25.3", sums: tuiFiles("v0.25.3")}
	withGitHub(t, f)
	st, err := Check(context.Background())
	if err != nil || st.Available {
		t.Fatalf("check = %+v, %v", st, err)
	}
	want := []string{"GET " + page + ".atom", "HEAD " + page + "/latest/download/" + tuiSums}
	if got := f.seen(); !slices.Equal(got, want) {
		t.Errorf("an up-to-date install asked for %q, want %q", got, want)
	}
	if len(st.Releases) != 3 {
		t.Errorf("/update list must still name every release, got %d", len(st.Releases))
	}
}

func TestSumsAreFetchedConcurrentlyAndBounded(t *testing.T) {
	withScriptInstall(t)
	var tags []string
	sums := map[string]string{}
	for rc := 12; rc >= 1; rc-- {
		tag := fmt.Sprintf("v0.25.4-rc%d", rc)
		tags = append(tags, tag)
		sums = merged(sums, tuiFiles(tag))
	}
	f := &fakeGitHub{feed: atom(tags...), latest: "v0.25.3", sums: sums, delay: 5 * time.Millisecond}
	withGitHub(t, f)
	if _, err := ListReleases(context.Background(), "0.25.0"); err != nil {
		t.Fatal(err)
	}
	// 8 checksum reads at most, plus the feed and the latest route.
	if f.mostAtOne < 4 || f.mostAtOne > 10 {
		t.Errorf("%d requests at once; want between 4 and 10", f.mostAtOne)
	}
}

func TestNoGitHubTokenIsSent(t *testing.T) {
	withScriptInstall(t)
	t.Setenv("GH_TOKEN", "abc")
	var mu sync.Mutex
	var sent []string
	f := &fakeGitHub{feed: atom("v0.25.2"), latest: "v0.25.2", sums: tuiFiles("v0.25.2")}
	httpClientPrev := httpClient
	httpClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		mu.Lock()
		sent = append(sent, r.Header.Get("Authorization"))
		mu.Unlock()
		return f.RoundTrip(r)
	})}
	t.Cleanup(func() { httpClient = httpClientPrev })
	if _, err := ListReleases(context.Background(), "0.25.0"); err != nil {
		t.Fatal(err)
	}
	for _, a := range sent {
		if a != "" {
			t.Errorf("a token went to a public route: %q", a)
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestTagForMatchesReleaseTags(t *testing.T) {
	for version, want := range map[string]string{
		"0.25.4":      "v0.25.4",
		"0.25.4-rc.2": "v0.25.4-rc2",
		"v0.25.4-rc2": "v0.25.4-rc2",
	} {
		if got := TagFor(version); got != want || !ValidTag(got) {
			t.Errorf("TagFor(%q) = %q, want %q", version, got, want)
		}
	}
}
