// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// TestMain keeps every test in the package off the user's real GAIA home and
// real daemon: a test that "follows GAIA's server" must never find theirs.
func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "gaia-lemonade-test-")
	if err != nil {
		panic(err)
	}
	os.Setenv("GAIA_HOME", home)
	// A developer's own Local only must not change what these tests send.
	os.Setenv("GAIA_POLICY_FILE", filepath.Join(home, "policy.json"))
	os.Setenv("LEMONADE_BASE_URL", "")
	os.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	os.Setenv("GAIA_CONFIG_FILE", "")
	os.Setenv("GAIA_CONFIG_DIR", "")
	ensureEmbedded = func(context.Context) error {
		return errors.New("test: no daemon")
	}
	code := m.Run()
	os.RemoveAll(home)
	os.Exit(code)
}

// requestLog records every request a client makes, wherever it is addressed —
// including the default port, which a test must never bind.
type requestLog struct {
	mu   sync.Mutex
	seen []string
}

func (l *requestLog) RoundTrip(r *http.Request) (*http.Response, error) {
	body := ""
	if r.Body != nil {
		raw, _ := io.ReadAll(r.Body)
		body = string(raw)
		r.Body = io.NopCloser(strings.NewReader(body))
	}
	l.mu.Lock()
	l.seen = append(l.seen, r.URL.Host+r.URL.Path+" "+body)
	l.mu.Unlock()
	return http.DefaultTransport.RoundTrip(r)
}

func (l *requestLog) to(host string) []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	var out []string
	for _, s := range l.seen {
		if strings.HasPrefix(s, host) {
			out = append(out, s)
		}
	}
	return out
}

// gaiaHomeWithEmbedded is a GAIA home where GAIA's own Lemonade is installed
// but not running: exactly the state mid-restart.
func gaiaHomeWithEmbedded(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("GAIA_HOME", dir)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	dist := filepath.Join(dir, "lemonade", "dist", "2026.39.1")
	if err := os.MkdirAll(dist, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dist, "lemond.exe"), nil, 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

// acceptingLemonade answers every route, as someone else's Lemonade would.
func acceptingLemonade(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func stubDaemon(t *testing.T, fn func(context.Context) error) *int {
	t.Helper()
	calls := 0
	restore := StubEnsureEmbeddedForTest(func(ctx context.Context) error {
		calls++
		return fn(ctx)
	})
	t.Cleanup(restore)
	return &calls
}

var fireworks = Provider{Name: "fireworks", BaseURL: FireworksURL, Header: "Authorization", Prefix: "Bearer "}

func TestCloudKeyIsNotSentAnywhereWhileGAIAsLemonadeRestarts(t *testing.T) {
	gaiaHomeWithEmbedded(t)
	calls := stubDaemon(t, func(context.Context) error { return errors.New("still starting") })
	foreign := acceptingLemonade(t)
	foreignHost := strings.TrimPrefix(foreign.URL, "http://")

	for _, base := range []string{"", foreign.URL + "/api/v1"} {
		log := &requestLog{}
		c := New(base)
		c.HTTP.Transport = log
		err := c.Configure(context.Background(), fireworks, "fw-secret")
		if !errors.Is(err, ErrRestarting) {
			t.Fatalf("base %q: err = %v, want ErrRestarting", base, err)
		}
		if len(log.seen) != 0 {
			t.Fatalf("base %q: requests sent during the restart: %v", base, log.seen)
		}
		if got := log.to(foreignHost); len(got) != 0 {
			t.Fatalf("someone else's Lemonade got %v", got)
		}
	}
	if *calls != 2 {
		t.Fatalf("daemon asked %d times, want once per attempt", *calls)
	}
}

func TestCloudKeyFollowsGAIAsLemonadeOnceTheDaemonBringsItBack(t *testing.T) {
	dir := gaiaHomeWithEmbedded(t)
	foreign := acceptingLemonade(t)
	var ownAuth []string
	own := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ownAuth = append(ownAuth, r.URL.Path+" "+r.Header.Get("Authorization"))
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(own.Close)
	u, _ := url.Parse(own.URL)
	port, _ := strconv.Atoi(u.Port())
	stubDaemon(t, func(context.Context) error {
		writeEmbeddedState(t, dir, port, "restarted-key")
		return nil
	})

	log := &requestLog{}
	// A stale address from before the restart, where another server now answers.
	c := New(foreign.URL + "/api/v1")
	c.HTTP.Transport = log
	if err := c.Configure(context.Background(), fireworks, "fw-secret"); err != nil {
		t.Fatal(err)
	}
	if got := log.to(strings.TrimPrefix(foreign.URL, "http://")); len(got) != 0 {
		t.Fatalf("the stale address got %v", got)
	}
	want := "/api/v1/install Bearer restarted-key,/api/v1/cloud/auth Bearer restarted-key"
	if strings.Join(ownAuth, ",") != want {
		t.Fatalf("GAIA's server got %v, want %s", ownAuth, want)
	}
}

// providerServer answers /system-info with one provider holding a key, and
// records each request's path and bearer.
func providerServer(t *testing.T) (*httptest.Server, *[]string) {
	t.Helper()
	var seen []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = append(seen, r.URL.Path+" "+r.Header.Get("Authorization"))
		_, _ = w.Write([]byte(`{"cloud":{"providers":[{"name":"amd","runtime_key_set":true}]}}`))
	}))
	t.Cleanup(srv.Close)
	return srv, &seen
}

func portOf(t *testing.T, srv *httptest.Server) int {
	t.Helper()
	u, _ := url.Parse(srv.URL)
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatal(err)
	}
	return port
}

// The /provider panel opens on the address the agent last reported. Once
// GAIA's server restarted on a new port, reading from the old one said
// "Lemonade did not respond" and showed every key as missing.
func TestReadsFollowGAIAsLemonadeToItsNewPort(t *testing.T) {
	dir := gaiaHomeWithEmbedded(t)
	own, seen := providerServer(t)
	writeEmbeddedState(t, dir, portOf(t, own), "restarted-key")

	got, err := New("http://localhost:1/api/v1").Providers(context.Background())
	if err != nil {
		t.Fatalf("err = %v, want the providers of GAIA's restarted server", err)
	}
	if len(got) != 1 || !got[0].RuntimeKey {
		t.Fatalf("providers = %+v", got)
	}
	if want := "/api/v1/system-info Bearer restarted-key"; strings.Join(*seen, ",") != want {
		t.Fatalf("GAIA's server got %v, want %s", *seen, want)
	}
}

func TestReadsStayPutWhenGAIAsLemonadeIsNotTheTarget(t *testing.T) {
	t.Run("mid-restart", func(t *testing.T) {
		gaiaHomeWithEmbedded(t)
		srv, seen := providerServer(t)
		if _, err := New(srv.URL + "/api/v1").Providers(context.Background()); err != nil || len(*seen) != 1 {
			t.Fatalf("err = %v, requests %v", err, *seen)
		}
	})
	t.Run("LEMONADE_BASE_URL chosen", func(t *testing.T) {
		dir := gaiaHomeWithEmbedded(t)
		own, ownSeen := providerServer(t)
		writeEmbeddedState(t, dir, portOf(t, own), "k")
		chosen, seen := providerServer(t)
		t.Setenv("LEMONADE_BASE_URL", chosen.URL)
		if _, err := New(chosen.URL).Providers(context.Background()); err != nil || len(*seen) != 1 || len(*ownSeen) != 0 {
			t.Fatalf("err = %v, chosen got %v, GAIA's got %v", err, *seen, *ownSeen)
		}
	})
}

func TestExplicitLemonadeBaseURLStillGetsTheKey(t *testing.T) {
	gaiaHomeWithEmbedded(t)
	calls := stubDaemon(t, func(context.Context) error { return errors.New("must not be asked") })
	var body string
	chosen := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/cloud/auth" {
			raw, _ := io.ReadAll(r.Body)
			body = string(raw)
		}
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(chosen.Close)
	t.Setenv("LEMONADE_BASE_URL", chosen.URL)

	if err := New("").Configure(context.Background(), fireworks, "fw-secret"); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(body, "fw-secret") {
		t.Fatalf("the configured server did not get the key: %q", body)
	}
	if *calls != 0 {
		t.Fatal("a chosen server must not wait on GAIA's daemon")
	}
}

func TestCredentialTargetRules(t *testing.T) {
	ctx := context.Background()

	t.Run("system Lemonade when GAIA's is not installed", func(t *testing.T) {
		t.Setenv("GAIA_HOME", t.TempDir())
		calls := stubDaemon(t, func(context.Context) error { return nil })
		got, err := CredentialBaseURL(ctx, "")
		if err != nil || got != DefaultBaseURL || *calls != 0 {
			t.Fatalf("got %q, %v, daemon calls %d", got, err, *calls)
		}
	})

	t.Run("another address while GAIA runs Lemonade is refused", func(t *testing.T) {
		gaiaHomeWithEmbedded(t)
		_, err := CredentialBaseURL(ctx, "https://router.example")
		if err == nil || !strings.Contains(err.Error(), "LEMONADE_BASE_URL") {
			t.Fatalf("err = %v", err)
		}
	})

	t.Run("GAIA's own exported URL is not a user choice", func(t *testing.T) {
		dir := gaiaHomeWithEmbedded(t)
		writeEmbeddedState(t, dir, 63210, "k")
		t.Setenv("LEMONADE_BASE_URL", "http://localhost:13305")
		t.Setenv("GAIA_LEMONADE_EMBEDDED", "1")
		got, err := CredentialBaseURL(ctx, "")
		if err != nil || got != "http://localhost:63210/api/v1" {
			t.Fatalf("got %q, %v", got, err)
		}
	})

	t.Run("the daemon answering without a server is still a restart", func(t *testing.T) {
		gaiaHomeWithEmbedded(t)
		stubDaemon(t, func(context.Context) error { return nil })
		if _, err := CredentialBaseURL(ctx, ""); !errors.Is(err, ErrRestarting) {
			t.Fatalf("err = %v", err)
		}
	})
}
