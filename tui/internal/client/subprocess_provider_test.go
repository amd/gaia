// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
)

func TestSetModelBeforeStartReplacesOnlyInferenceFlags(t *testing.T) {
	for _, args := range [][]string{
		{"--json-events", "--dev", "--use-claude", "--claude-model", "claude-sonnet-5", "--bypass-permissions", "--model", "old-model"},
		{"--json-events", "--dev", "--use-claude=true", "--claude-model=claude-sonnet-5", "--bypass-permissions", "--model=old-model"},
	} {
		original := append([]string(nil), args...)
		c := NewCanonicalSubprocessClient("unused", args, true)
		if !c.SetModelBeforeStart("fireworks.gemma-4-31b-it") {
			t.Fatal("catalog selection was refused before startup")
		}
		want := []string{"--json-events", "--dev", "--bypass-permissions", "--model", "fireworks.gemma-4-31b-it"}
		if !reflect.DeepEqual(c.args, want) || !reflect.DeepEqual(args, original) {
			t.Fatalf("inference flags or caller's arguments were changed incorrectly: %v", c.args)
		}
		if c.ClaudeAtLaunch() || c.ClaudeModelAtLaunch() != "" || !c.BypassAtLaunch() || c.ModelAtLaunch() != "fireworks.gemma-4-31b-it" {
			t.Fatal("launch getters disagree with the selected provider")
		}
		if !c.SetModelBeforeStart("amd.gpt-4.1") || c.ModelAtLaunch() != "amd.gpt-4.1" {
			t.Fatal("second selection retained a stale launch model")
		}
	}
}

func TestSetModelBeforeStartRefusesRunningLegacyAndEmptySelections(t *testing.T) {
	for _, tc := range []struct {
		name, model        string
		canonical, started bool
	}{
		{"running conversation", "amd.gpt-4.1", true, true},
		{"legacy agent", "amd.gpt-4.1", false, false},
		{"empty selection", "  ", true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := NewSubprocessClient("unused", []string{"--model", "existing"}, false)
			c.canonical, c.started = tc.canonical, tc.started
			if c.SetModelBeforeStart(tc.model) || c.ModelAtLaunch() != "existing" {
				t.Fatal("unsupported switch changed the launch arguments")
			}
		})
	}
}

func TestLaunchModelSettersAndGettersAreSafeTogether(t *testing.T) {
	c := NewCanonicalSubprocessClient("unused", []string{"--use-claude", "--claude-model", "claude-sonnet-5", "--bypass-permissions"}, false)
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				c.SetModelBeforeStart("fireworks.gemma-4-31b-it")
				c.ModelAtLaunch()
				c.ClaudeAtLaunch()
				c.ClaudeModelAtLaunch()
				c.BypassAtLaunch()
			}
		}()
	}
	wg.Wait()
	if c.ModelAtLaunch() != "fireworks.gemma-4-31b-it" || c.ClaudeAtLaunch() {
		t.Fatal("concurrent access left stale model flags")
	}
}

func isolateSubprocessConnection(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("GAIA_HOME", dir)
	t.Setenv("LEMONADE_BASE_URL", "")
	t.Setenv("LEMONADE_API_KEY", "")
	return dir
}

func replaceSubprocessPorts(t *testing.T, serverURL string) {
	t.Helper()
	u, err := url.Parse(serverURL)
	if err != nil {
		t.Fatal(err)
	}
	old := subprocessLemonadePorts
	subprocessLemonadePorts = []string{u.Port()}
	t.Cleanup(func() { subprocessLemonadePorts = old })
}

func TestPendingCloudSelectionLaunchesAgainstEmbeddedEndpoint(t *testing.T) {
	dir := isolateSubprocessConnection(t)
	private := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("recorded private endpoint needs no rediscovery during spawn")
	}))
	defer private.Close()
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("unrelated fixed-port server must not override embedded routing")
		_, _ = w.Write([]byte(`{"data":[]}`))
	}))
	defer other.Close()
	replaceSubprocessPorts(t, other.URL)
	u, _ := url.Parse(private.URL)
	port, _ := strconv.Atoi(u.Port())
	dir = filepath.Join(dir, "lemonade")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]any{"port": port, "api_key": "embedded-private-key"})
	if err := os.WriteFile(filepath.Join(dir, "state.json"), body, 0o600); err != nil {
		t.Fatal(err)
	}
	c := NewCanonicalSubprocessClient(buildMockAgent(t), []string{"--json-events", "--use-claude"}, false)
	if !c.SetModelBeforeStart("fireworks.gemma-4-31b-it") {
		t.Fatal("could not select cloud before launch")
	}
	c.mu.Lock()
	st, err := c.startLocked()
	c.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = st.stdin.Close()
		st.proc.kill()
		st.proc.reap()
		c.discard(st.proc, nil)
		close(st.turnDone)
	})
	if !reflect.DeepEqual(st.proc.cmd.Args[1:], []string{"--json-events", "--model", "fireworks.gemma-4-31b-it"}) {
		t.Fatalf("actual child launch had incorrect model arguments: %v", st.proc.cmd.Args[1:])
	}
	var selectedURL string
	for _, env := range st.proc.cmd.Env {
		if strings.HasPrefix(env, "LEMONADE_BASE_URL=") {
			selectedURL = strings.TrimPrefix(env, "LEMONADE_BASE_URL=")
		}
	}
	if selectedURL != "http://localhost:"+u.Port()+"/api/v1" {
		t.Fatalf("child did not receive the selected embedded endpoint: %q", selectedURL)
	}
	if c.SetModelBeforeStart("amd.gpt-4.1") {
		t.Fatal("already-started child accepted a startup-only change")
	}
}

func TestSubprocessLegacyDiscoveryAuthenticatesAndRefusesRedirects(t *testing.T) {
	isolateSubprocessConnection(t)
	t.Setenv("LEMONADE_API_KEY", "explicit-router-key")
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("discovery followed a redirect")
	}))
	defer target.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer explicit-router-key" {
			t.Error("legacy probe omitted explicitly configured authentication")
		}
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer source.Close()
	replaceSubprocessPorts(t, source.URL)
	if detectLemonadeURL() != "" {
		t.Fatal("redirected discovery reported a usable server")
	}
}
