// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// anyRequest fails the test on every request the client makes, wherever it is
// addressed — so the default port is covered without binding it.
type anyRequest struct {
	mu   sync.Mutex
	seen []string
}

func (a *anyRequest) RoundTrip(r *http.Request) (*http.Response, error) {
	a.mu.Lock()
	a.seen = append(a.seen, r.URL.String())
	a.mu.Unlock()
	return nil, errors.New("test: no request may be sent")
}

// installedButStopped is a GAIA home whose own Lemonade is installed and not
// running, as it is while the daemon restarts it.
func installedButStopped(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("GAIA_HOME", dir)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	t.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	dist := filepath.Join(dir, "lemonade", "dist", "2026.39.1")
	if err := os.MkdirAll(dist, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dist, "lemond.exe"), nil, 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestGatewayTokenIsNotSentWhileGAIAsLemonadeRestarts(t *testing.T) {
	installedButStopped(t)
	asked := 0
	t.Cleanup(lemonade.StubEnsureEmbeddedForTest(func(context.Context) error {
		asked++
		return errors.New("still starting")
	}))

	c := NewClient()
	sent := &anyRequest{}
	c.http.Transport = sent
	_, err := c.SetToken("gw-secret", DefaultBaseURL)
	if !errors.Is(err, lemonade.ErrRestarting) {
		t.Fatalf("err = %v, want the restarting error", err)
	}
	if len(sent.seen) != 0 {
		t.Fatalf("the token went out during the restart: %v", sent.seen)
	}
	if asked != 1 {
		t.Fatalf("daemon asked %d times, want 1", asked)
	}
}

func TestGatewayTokenGoesToGAIAsLemonadeAfterTheRestart(t *testing.T) {
	dir := installedButStopped(t)
	var got []string
	own := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = append(got, r.URL.Path+" "+r.Header.Get("Authorization"))
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(own.Close)
	u, _ := url.Parse(own.URL)
	t.Cleanup(lemonade.StubEnsureEmbeddedForTest(func(context.Context) error {
		state := `{"port":` + u.Port() + `,"api_key":"restarted-key"}`
		return os.WriteFile(filepath.Join(dir, "lemonade", "state.json"), []byte(state), 0o600)
	}))

	if _, err := NewClient().SetToken("gw-secret", DefaultBaseURL); err != nil {
		t.Fatal(err)
	}
	if strings.Join(got, ",") != "/api/v1/cloud/auth Bearer restarted-key" {
		t.Fatalf("GAIA's server got %v", got)
	}
}

func TestGatewayTokenGoesToTheExplicitLemonadeBaseURL(t *testing.T) {
	installedButStopped(t)
	t.Cleanup(lemonade.StubEnsureEmbeddedForTest(func(context.Context) error {
		t.Error("a chosen server must not wait on GAIA's daemon")
		return nil
	}))
	var token string
	chosen := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/cloud/auth" {
			raw, _ := io.ReadAll(r.Body)
			var body map[string]string
			_ = json.Unmarshal(raw, &body)
			token = body["api_key"]
		}
		_, _ = w.Write([]byte(`{}`))
	}))
	t.Cleanup(chosen.Close)
	t.Setenv("LEMONADE_BASE_URL", chosen.URL)

	if _, err := NewClient().SetToken("gw-secret", DefaultBaseURL); err != nil {
		t.Fatal(err)
	}
	if token != "gw-secret" {
		t.Fatalf("the configured server got token %q", token)
	}
}
