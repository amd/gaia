// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

// useConfig points the config reader at a fresh file holding doc.
func useConfig(t *testing.T, doc map[string]any) string {
	t.Helper()
	isolatedConnection(t)
	path := filepath.Join(t.TempDir(), "config.json")
	t.Setenv("GAIA_CONFIG_FILE", path)
	t.Setenv("GAIA_LEMONADE_EMBEDDED", "")
	if doc != nil {
		raw, err := json.Marshal(doc)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return path
}

func external(url string, keyStored bool) map[string]any {
	return map[string]any{
		"lemonade_mode": "external", "lemonade_base_url": url, "lemonade_api_key_stored": keyStored,
		"default_model": "Gemma-4-E4B-it-GGUF",
	}
}

// installEmbeddedHere makes GAIA's own server look installed and running.
func installEmbeddedHere(t *testing.T, port int) {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "gaia")
	t.Setenv("GAIA_HOME", dir)
	dist := filepath.Join(dir, "lemonade", "dist", "1")
	if err := os.MkdirAll(dist, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dist, "lemond"), nil, 0o755); err != nil {
		t.Fatal(err)
	}
	writeEmbeddedState(t, dir, port, "embedded-key")
}

func TestTheSavedChoicePicksTheServer(t *testing.T) {
	for _, tc := range []struct {
		name     string
		doc      map[string]any
		env      string
		marker   bool
		want     string
		gaiaRuns bool
	}{
		{name: "external mode uses the saved server",
			doc: external("http://localhost:13305/api/v1", false), want: "http://localhost:13305/api/v1"},
		{name: "a bare saved origin gains the API path",
			doc: external("http://lemon.lan:9000", false), want: "http://lemon.lan:9000/api/v1"},
		{name: "LEMONADE_BASE_URL wins over the saved server",
			doc: external("http://localhost:13305/api/v1", false), env: "http://other:1/api/v1", want: "http://other:1/api/v1"},
		{name: "GAIA's own exported URL is not a choice, so the saved server wins",
			doc: external("http://localhost:13305/api/v1", false), env: "http://localhost:63207/api/v1", marker: true,
			want: "http://localhost:13305/api/v1"},
		{name: "GAIA's own exported URL follows its recorded state",
			doc: map[string]any{"lemonade_mode": "gaia"}, env: "http://localhost:1/api/v1", marker: true,
			want: "http://localhost:63207/api/v1", gaiaRuns: true},
		{name: "gaia mode uses GAIA's own",
			doc:  map[string]any{"lemonade_mode": "gaia", "lemonade_base_url": "http://localhost:13305/api/v1"},
			want: "http://localhost:63207/api/v1", gaiaRuns: true},
		{name: "no choice behaves as gaia",
			doc: nil, want: "http://localhost:63207/api/v1", gaiaRuns: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			useConfig(t, tc.doc)
			installEmbeddedHere(t, 63207)
			t.Setenv("LEMONADE_BASE_URL", tc.env)
			if tc.marker {
				t.Setenv("GAIA_LEMONADE_EMBEDDED", "1")
			}
			if got := ResolveBaseURL(""); got != tc.want {
				t.Errorf("ResolveBaseURL = %q, want %q", got, tc.want)
			}
			if got := New("").BaseURL; got != tc.want {
				t.Errorf("New(\"\").BaseURL = %q, want %q", got, tc.want)
			}
			if got := GAIARunsLemonade(); got != tc.gaiaRuns {
				t.Errorf("GAIARunsLemonade = %v, want %v", got, tc.gaiaRuns)
			}
		})
	}
}

// In external mode a credential call must never ask the daemon to start
// GAIA's own server, even though it is installed.
func TestExternalModeNeverEnsuresGAIAsServer(t *testing.T) {
	useConfig(t, external("http://localhost:13305/api/v1", false))
	installEmbeddedHere(t, 63207)
	calls := 0
	t.Cleanup(StubEnsureEmbeddedForTest(func(context.Context) error {
		calls++
		return nil
	}))
	os.Remove(filepath.Join(os.Getenv("GAIA_HOME"), "lemonade", "state.json"))

	base, err := CredentialBaseURL(context.Background(), "")
	if err != nil || base != "http://localhost:13305/api/v1" {
		t.Fatalf("CredentialBaseURL = %q, %v", base, err)
	}
	if calls != 0 {
		t.Fatalf("external mode asked the daemon to start GAIA's server %d times", calls)
	}
	if !ExternalModeActive() {
		t.Fatal("external mode not reported active")
	}
}

// external mode saved without a URL still starts nothing, and says why.
func TestExternalModeWithoutAURLIsAnErrorNotAFallback(t *testing.T) {
	useConfig(t, map[string]any{"lemonade_mode": "external"})
	installEmbeddedHere(t, 63207)
	_, active, err := SavedExternal()
	if !active || err == nil || !strings.Contains(err.Error(), "gaia lemonade connect") {
		t.Fatalf("SavedExternal active=%v err=%v", active, err)
	}
	if GAIARunsLemonade() {
		t.Fatal("a broken external choice let GAIA run its own server")
	}
}

func TestReadChoiceRefusesWhatPythonRefuses(t *testing.T) {
	for name, doc := range map[string]map[string]any{
		"unknown mode":      {"lemonade_mode": "mine"},
		"string key flag":   {"lemonade_api_key_stored": "yes"},
		"non-string url":    {"lemonade_base_url": 5},
		"non-string mode":   {"lemonade_mode": true},
		"python-shaped ok?": nil,
	} {
		t.Run(name, func(t *testing.T) {
			path := useConfig(t, doc)
			_, err := ReadChoice()
			if doc == nil {
				if err != nil {
					t.Fatalf("a missing config is not an error: %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), path) {
				t.Fatalf("err = %v, want one naming %s", err, path)
			}
		})
	}
	// Python writes every field, unset ones as null.
	useConfig(t, map[string]any{"lemonade_mode": nil, "lemonade_base_url": nil, "lemonade_api_key_stored": false})
	if c, err := ReadChoice(); err != nil || c.Mode != "" {
		t.Fatalf("nulls: %+v, %v", c, err)
	}
}

// The saved key comes from the daemon, once, and only for the saved server.
func TestTheSavedKeyGoesOnlyToTheSavedServer(t *testing.T) {
	var (
		mu       sync.Mutex
		gotAuth  = map[string]string{}
		fetches  = 0
		key      = "user-key"
		handlers = func(name string) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				gotAuth[name] = r.Header.Get("Authorization")
				mu.Unlock()
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprint(w, `{"data":[]}`)
			})
		}
	)
	saved := httptest.NewServer(handlers("saved"))
	defer saved.Close()
	other := httptest.NewServer(handlers("other"))
	defer other.Close()

	useConfig(t, external(saved.URL, true))
	t.Cleanup(StubDaemonTargetForTest(func(context.Context) (DaemonTarget, error) {
		fetches++
		base := saved.URL + "/api/v1"
		return DaemonTarget{Mode: "external", Source: "config", BaseURL: &base, APIKeyStored: true, APIKey: &key}, nil
	}))

	ctx := context.Background()
	if _, err := New("").Models(ctx, "local"); err != nil {
		t.Fatalf("saved server: %v", err)
	}
	if _, err := New(other.URL).Models(ctx, "local"); err != nil {
		t.Fatalf("other server: %v", err)
	}
	if gotAuth["saved"] != "Bearer user-key" {
		t.Errorf("saved server got Authorization %q", gotAuth["saved"])
	}
	if gotAuth["other"] != "" {
		t.Errorf("an unrelated server got the saved key: %q", gotAuth["other"])
	}
	if _, err := APIKeyFor(ctx, saved.URL); err != nil {
		t.Fatal(err)
	}
	if fetches != 1 {
		t.Errorf("daemon asked %d times, want once per process", fetches)
	}
}

func TestASavedKeyThatCannotBeFetchedIsAnError(t *testing.T) {
	useConfig(t, external("http://localhost:13305/api/v1", true))
	for name, fetch := range map[string]func(context.Context) (DaemonTarget, error){
		"daemon unreachable": func(context.Context) (DaemonTarget, error) {
			return DaemonTarget{}, errors.New("no daemon")
		},
		"daemon resolves another server": func(context.Context) (DaemonTarget, error) {
			base := "http://elsewhere:1/api/v1"
			return DaemonTarget{Mode: "external", Source: "env", BaseURL: &base}, nil
		},
	} {
		t.Run(name, func(t *testing.T) {
			t.Cleanup(StubDaemonTargetForTest(fetch))
			key, err := APIKeyFor(context.Background(), "")
			if err == nil || key != "" {
				t.Fatalf("APIKeyFor = %q, %v; want an error and no key", key, err)
			}
			if !strings.Contains(err.Error(), "http://localhost:13305/api/v1") {
				t.Errorf("error does not name the server: %v", err)
			}
		})
	}
	// An explicit environment key needs no daemon at all.
	t.Cleanup(StubDaemonTargetForTest(func(context.Context) (DaemonTarget, error) {
		t.Fatal("the daemon was asked although LEMONADE_API_KEY is set")
		return DaemonTarget{}, nil
	}))
	t.Setenv("LEMONADE_API_KEY", "env-key")
	if key, err := APIKeyFor(context.Background(), ""); err != nil || key != "env-key" {
		t.Fatalf("env key: %q, %v", key, err)
	}
}

func TestProbeTellsLemonadeFromEverythingElse(t *testing.T) {
	for _, tc := range []struct {
		name, key, contentType, body string
		status                       int
		want                         string
		inMessage                    []string
	}{
		{name: "current lemonade", status: 200, contentType: "application/json",
			body: `{"status":"ok","version":"10.10.0"}`, want: ProbeOK},
		{name: "too old", status: 200, contentType: "application/json",
			body: `{"status":"ok","version":"10.8.1"}`, want: ProbeTooOld,
			inMessage: []string{"v10.8.1", "v10.9.0", "https://lemonade-server.ai", "gaia config set lemonade_mode gaia"}},
		{name: "needs a key", status: 401, want: ProbeNeedsKey, inMessage: []string{"gaia lemonade connect"}},
		{name: "rejects the key", key: "k", status: 403, want: ProbeBadKey, inMessage: []string{"gaia lemonade connect"}},
		{name: "a web page", status: 200, contentType: "text/html", body: "<html>hi</html>",
			want: ProbeNotLemonade, inMessage: []string{"not a Lemonade Server", "text/html"}},
		{name: "other JSON", status: 200, contentType: "application/json", body: `{"status":"ok"}`,
			want: ProbeNotLemonade},
		{name: "other JSON with a version", status: 200, contentType: "application/json",
			body: `{"healthy":true,"version":"11.0.0"}`, want: ProbeNotLemonade},
		{name: "an error page", status: 500, want: ProbeNotLemonade},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var auth string
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/v1/health" {
					t.Errorf("probed %s, want /api/v1/health", r.URL.Path)
				}
				auth = r.Header.Get("Authorization")
				if tc.contentType != "" {
					w.Header().Set("Content-Type", tc.contentType)
				}
				w.WriteHeader(tc.status)
				fmt.Fprint(w, tc.body)
			}))
			defer srv.Close()
			r := Probe(context.Background(), srv.URL, tc.key)
			if r.Status != tc.want {
				t.Fatalf("status = %s, want %s (%+v)", r.Status, tc.want, r)
			}
			if tc.key != "" && auth != "Bearer "+tc.key {
				t.Errorf("Authorization = %q", auth)
			}
			if tc.key == "" && auth != "" {
				t.Errorf("a key was sent although none was given: %q", auth)
			}
			for _, want := range tc.inMessage {
				if !strings.Contains(r.Message(), want) {
					t.Errorf("message %q lacks %q", r.Message(), want)
				}
			}
		})
	}

	srv := httptest.NewServer(http.NotFoundHandler())
	url := srv.URL
	srv.Close()
	r := Probe(context.Background(), url, "")
	if r.Status != ProbeUnreachable {
		t.Fatalf("closed port: %+v", r)
	}
	for _, want := range []string{"is not answering", "Start it", "gaia config set lemonade_mode gaia"} {
		if !strings.Contains(r.Message(), want) {
			t.Errorf("down message %q lacks %q", r.Message(), want)
		}
	}
}

func TestApplyingTheChoiceToTheDaemon(t *testing.T) {
	answer := func(mode, base string) []byte {
		raw, _ := json.Marshal(map[string]any{"mode": mode, "base_url": base, "action": "stopped"})
		return raw
	}
	for _, tc := range []struct {
		name    string
		reply   []byte
		err     error
		wantErr string
	}{
		{name: "no daemon running", err: fmt.Errorf("could not reach the GAIA daemon: %w", &daemon.NotRunningError{})},
		{name: "daemon follows", reply: answer("external", "http://localhost:13305/api/v1")},
		{name: "daemon too old", err: &daemon.RouteMissingError{Path: lemonadeReconfigurePath}, wantErr: "gaia daemon restart"},
		{name: "daemon refuses", err: errors.New("HTTP 409: not usable"), wantErr: "gaia daemon restart"},
		{name: "daemon still elsewhere", reply: answer("external", "http://other:1/api/v1"), wantErr: "gaia daemon restart"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			t.Cleanup(StubReconfigureForTest(func(context.Context) ([]byte, error) {
				calls++
				return tc.reply, tc.err
			}))
			err := ApplyChoiceToDaemon(context.Background(), ModeExternal, "http://localhost:13305/api/v1")
			if calls != 1 {
				t.Fatalf("reconfigure called %d times", calls)
			}
			if tc.wantErr == "" && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if tc.wantErr != "" && (err == nil || !strings.Contains(err.Error(), tc.wantErr)) {
				t.Fatalf("err = %v, want one containing %q", err, tc.wantErr)
			}
		})
	}
}

// The adoption floor is the flagship profile's, or GAIA adopts a server its
// own setup would refuse.
func TestMinExternalVersionMatchesTheGaiaProfile(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "src", "gaia", "installer", "init_command.py"))
	if err != nil {
		t.Skipf("Python source not alongside this checkout: %v", err)
	}
	block := regexp.MustCompile(`(?s)\n    "gaia": \{.*?"min_lemonade_version": "([^"]+)"`).FindSubmatch(raw)
	if block == nil {
		t.Fatal("could not find the gaia profile's min_lemonade_version in init_command.py")
	}
	if got := string(block[1]); got != MinExternalVersion {
		t.Fatalf("MinExternalVersion = %s, but the gaia profile needs %s", MinExternalVersion, got)
	}
}

// An unreadable choice must stop everything Python would refuse: no start of
// GAIA's server, no request, no quiet fall back to the default port.
func TestAnUnreadableChoiceStartsNothingAndSaysWhy(t *testing.T) {
	for name, body := range map[string]string{
		"invalid mode": `{"lemonade_mode":"mine"}`,
		"invalid JSON": `{"lemonade_mode":`,
	} {
		t.Run(name, func(t *testing.T) {
			path := useConfig(t, nil)
			if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
				t.Fatal(err)
			}
			installEmbeddedHere(t, 63207)
			os.Remove(filepath.Join(os.Getenv("GAIA_HOME"), "lemonade", "state.json"))
			ensures := 0
			t.Cleanup(StubEnsureEmbeddedForTest(func(context.Context) error {
				ensures++
				return nil
			}))

			if !ExternalModeActive() || GAIARunsLemonade() {
				t.Fatal("an unreadable choice let GAIA run its own server")
			}
			if _, err := CredentialBaseURL(context.Background(), ""); err == nil || !strings.Contains(err.Error(), path) {
				t.Fatalf("CredentialBaseURL err = %v, want one naming %s", err, path)
			}
			if _, err := APIKeyFor(context.Background(), ""); err == nil {
				t.Fatal("APIKeyFor hid the config error")
			}
			if _, err := New("").Models(context.Background(), "local"); err == nil {
				t.Fatal("a request went out despite the config error")
			}
			if ensures != 0 {
				t.Fatalf("asked the daemon to start GAIA's server %d times", ensures)
			}
			if name == "invalid mode" {
				msg := ChoiceError().Error()
				if !strings.Contains(msg, "Fix or delete the lemonade_mode key in "+path) || strings.Contains(msg, "gaia config set") {
					t.Errorf("message names a command that cannot load this config: %q", msg)
				}
			}
		})
	}
	// LEMONADE_BASE_URL overrides the choice without reading it, as in Python.
	path := useConfig(t, nil)
	if err := os.WriteFile(path, []byte(`{"lemonade_mode":`), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LEMONADE_BASE_URL", "http://lemon.lan:9000")
	if err := ChoiceError(); err != nil {
		t.Fatalf("env override still read the config: %v", err)
	}
}

// `gaia lemonade connect` re-saves the key and rewrites config.json; an open
// TUI must pick up the new key, not keep sending the old one.
func TestAResavedKeyIsRefetched(t *testing.T) {
	path := useConfig(t, external("http://localhost:13305/api/v1", true))
	keys := []string{"old-key", "new-key"}
	fetches := 0
	t.Cleanup(StubDaemonTargetForTest(func(context.Context) (DaemonTarget, error) {
		k := keys[fetches]
		fetches++
		return DaemonTarget{Mode: "external", Source: "config", APIKeyStored: true, APIKey: &k}, nil
	}))
	ctx := context.Background()
	for i := 0; i < 2; i++ {
		if key, err := APIKeyFor(ctx, ""); err != nil || key != "old-key" {
			t.Fatalf("first key: %q, %v", key, err)
		}
	}
	if fetches != 1 {
		t.Fatalf("fetched %d times before the re-save, want 1", fetches)
	}
	doc := external("http://localhost:13305/api/v1", true)
	doc["note"] = "rewritten by gaia lemonade connect"
	raw, _ := json.Marshal(doc)
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if key, err := APIKeyFor(ctx, ""); err != nil || key != "new-key" {
		t.Fatalf("after re-save: %q, %v; want the new key", key, err)
	}
}
