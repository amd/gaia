// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybridapi

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
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

const testToken = "test-token"

// fakeDaemon registers a loopback server as this machine's daemon: it answers
// the status probe as GAIA's and hands every other request to h.
func fakeDaemon(t *testing.T, h http.HandlerFunc) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if r.URL.Path == daemon.APIPrefix+"/status" {
			_ = json.NewEncoder(w).Encode(map[string]any{"service": daemon.ServiceID, "pid": os.Getpid()})
			return
		}
		h(w, r)
	}))
	t.Cleanup(srv.Close)
	u, _ := url.Parse(srv.URL)
	port, _ := strconv.Atoi(u.Port())
	home := t.TempDir()
	t.Setenv(daemon.EnvHome, home)
	raw, _ := json.Marshal(map[string]any{"pid": os.Getpid(), "port": port, "token": testToken,
		"host": "127.0.0.1", "api_version": daemon.RequiredAPIVersion(), "service": daemon.ServiceID})
	if err := os.WriteFile(filepath.Join(home, "instance.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return srv.URL
}

func TestUpdateSendsOnlyTheChangesToTheDaemon(t *testing.T) {
	var got map[string]any
	var method, path string
	fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&got)
		_, _ = io.WriteString(w, `{"cheap_model":"amd.a","policy":"balanced","classifier_timeout":15,"kinds":["other"]}`)
	})
	s, err := NewAttached().Update(context.Background(), map[string]any{"policy": "balanced"})
	if err != nil {
		t.Fatal(err)
	}
	if method != http.MethodPut || path != "/api/hybrid/settings" || len(got) != 1 || got["policy"] != "balanced" {
		t.Fatalf("%s %s %v", method, path, got)
	}
	if s.Policy != "balanced" || s.ClassifierTimeout != "15" || s.CheapModel != "amd.a" {
		t.Fatalf("settings %+v", s)
	}
}

func TestARefusalIsTheBackendsOwnWords(t *testing.T) {
	msg := "cheap_model: The AMD LLM Gateway doesn't list amd.nope. Checked: AMD LLM Gateway."
	fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		_ = json.NewEncoder(w).Encode(map[string]string{"detail": msg})
	})
	_, err := NewAttached().Update(context.Background(), map[string]any{"cheap_model": "amd.nope"})
	if err == nil || err.Error() != msg || !IsStatus(err, 422) {
		t.Fatalf("err = %v", err)
	}
}

func TestADaemonWithoutTheRoutesIsNamedAsOutdated(t *testing.T) {
	base := fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"detail":"Not Found"}`)
	})
	_, err := NewAttached().Settings(context.Background())
	var old *OutdatedError
	if !errors.As(err, &old) || !strings.Contains(err.Error(), base) || !strings.Contains(err.Error(), "Update GAIA") {
		t.Fatalf("err = %v", err)
	}
}

// A 404 the route itself sends (an unknown template) is its answer, not an old daemon.
func TestARoutesOwn404IsItsAnswer(t *testing.T) {
	fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"detail":"There is no hybrid template 'x'."}`)
	})
	_, err := NewAttached().ApplyTemplate(context.Background(), "x")
	if !IsStatus(err, 404) || !strings.Contains(err.Error(), "no hybrid template") {
		t.Fatalf("err = %v", err)
	}
}

func TestASlowDaemonNamesWhatAndWhere(t *testing.T) {
	release := make(chan struct{})
	base := fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	})
	defer close(release)
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	_, err := NewAttached().Stats(ctx)
	var slow *TimeoutError
	if !errors.As(err, &slow) || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err = %v", err)
	}
	for _, want := range []string{"Reading the routing stats", base, "didn't answer within"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("%q missing from %v", want, err)
		}
	}
}

func TestACancelledCallIsPassedThrough(t *testing.T) {
	release := make(chan struct{})
	fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	})
	defer close(release)
	ctx, cancel := context.WithCancel(context.Background())
	go func() { time.Sleep(100 * time.Millisecond); cancel() }()
	_, err := NewAttached().Preview(ctx, "hi")
	if err != context.Canceled {
		t.Fatalf("err = %v, want context.Canceled as is", err)
	}
}

func TestNoDaemonSaysWhatToDoInAppTerms(t *testing.T) {
	t.Setenv(daemon.EnvHome, t.TempDir())
	_, err := NewAttached().Entry(context.Background())
	var down *UnreachableError
	if !errors.As(err, &down) {
		t.Fatalf("err = %v", err)
	}
	text := err.Error()
	if !strings.Contains(text, "isn't running") || !strings.Contains(text, "Quit and reopen GAIA") {
		t.Fatalf("err = %v", text)
	}
	if strings.Contains(text, "`gaia ") {
		t.Fatalf("the remedy names a CLI command: %v", text)
	}
}

func TestStatsAcceptOnlyKnownScopes(t *testing.T) {
	for scope, ok := range map[string]bool{"agent_ui_chats": true, "all": true, "agent_ui": false, "": false} {
		fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) {
			_ = json.NewEncoder(w).Encode(map[string]any{"turns": 0, "scope": scope})
		})
		_, err := NewAttached().Stats(context.Background())
		if (err == nil) != ok {
			t.Errorf("scope %q: err = %v", scope, err)
		}
	}
}

func TestPresetsKeepTheBackendsOrder(t *testing.T) {
	var s Settings
	raw := `{"presets":{"quality":{"other":{"cheap_max_difficulty":0,"allow_high_stakes":false}},
		"balanced":{"other":{"cheap_max_difficulty":3,"allow_high_stakes":true}}},
		"policy":7,"classifier_timeout":"abc","cheap_model":null}`
	if err := json.Unmarshal([]byte(raw), &s); err != nil {
		t.Fatal(err)
	}
	presets, err := s.PresetList()
	if err != nil || len(presets) != 2 || presets[0].Name != "quality" || presets[1].Name != "balanced" ||
		presets[1].Rules["other"].CheapMaxDifficulty != 3 {
		t.Fatalf("%+v %v", presets, err)
	}
	// A hand-broken value is still shown as saved.
	if s.Policy != "7" || s.ClassifierTimeout != "abc" || s.CheapModel != "" {
		t.Fatalf("%+v", s)
	}
}

func TestStatsLine(t *testing.T) {
	cost, all, saved := 0.004, 0.02, 80.0
	for _, tc := range []struct {
		s    Stats
		want string
	}{
		{Stats{Scope: ScopeAgentUIChats}, "No hybrid turns recorded yet (Agent UI chats only)."},
		{Stats{Scope: ScopeAll, ScopeLabel: "Every chat."}, "No hybrid turns recorded yet (Every chat)."},
		{Stats{Scope: ScopeAll, Turns: 3, CheapTurns: 2, StrongTurns: 1, Cost: &cost, CostIfAllStrong: &all, SavingPct: &saved},
			"All chats, 3 turns: 2 on the cheap model, 1 on the strong · $0.0040 spent, $0.02 if all strong · 80% saved"},
		{Stats{Scope: ScopeAll, Turns: 1, StrongTurns: 1, UnpricedTurns: 1},
			"All chats, 1 turn: 0 on the cheap model, 1 on the strong · cost unknown, unknown if all strong · 1 turn at an unknown price"},
	} {
		if got := tc.s.Line(); got != tc.want {
			t.Errorf("got  %q\nwant %q", got, tc.want)
		}
	}
}

// A reply captured from a daemon's GET /api/hybrid/settings (model list trimmed).
func TestARealSettingsReplyDecodes(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "settings_reply.json"))
	if err != nil {
		t.Fatal(err)
	}
	var s Settings
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatal(err)
	}
	presets, err := s.PresetList()
	if err != nil || len(presets) != 2 || presets[0].Name != "quality" || presets[1].Name != "balanced" {
		t.Fatalf("presets %+v %v", presets, err)
	}
	rules, err := s.RuleTable()
	if err != nil || len(rules) != len(s.Kinds) || len(s.Kinds) != 8 {
		t.Fatalf("rules %v kinds %v err %v", rules, s.Kinds, err)
	}
	if s.Entry == nil || s.Entry.Label() != "Hybrid (DeepSeek + Opus) (cloud)" || !s.Entry.Shown {
		t.Fatalf("entry %+v", s.Entry)
	}
	if s.ClassifierTimeout != "15" || s.Policy != "quality" || len(s.Models) != 3 ||
		s.Models[0].Provider != "local" || s.Models[0].Available || s.Models[0].Reason == nil {
		t.Fatalf("settings %+v", s)
	}
}

func TestARefusedCredentialSaysWhatToDoInAppTerms(t *testing.T) {
	fakeDaemon(t, func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusUnauthorized) })
	_, err := NewAttached().Settings(context.Background())
	var down *UnreachableError
	if !errors.As(err, &down) || !strings.Contains(err.Error(), "stopped accepting this app's credentials") {
		t.Fatalf("err = %v", err)
	}
	if strings.Contains(err.Error(), "`gaia ") {
		t.Fatalf("the remedy names a CLI command: %v", err)
	}
}
