// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// #3978's bug never reproduced under catalog.SetMockBinary, because that
// helper (catalog.go) points the flagship at a mock binary WITHOUT ever
// calling LoadInstalledAgents -- so a --mock run never sees the transport
// flip a real hub install produces. This test drives the real path instead: a
// genuine ".installed" sentinel with "artifact_kind":"binary" on disk, loaded
// through the same catalog + client.ForAgent + chat wiring a real launch uses.

// memorySidecarFake answers the two daemon routes a real /memory fetch needs
// once the flagship is running as a daemon-supervised sidecar: the ensure
// call that attaches to it, and the relayed /version + /memory calls
// negotiate() and FetchMemory make against it.
type memorySidecarFake struct {
	srv   *httptest.Server
	token string
}

func newMemorySidecarFake(t *testing.T) *memorySidecarFake {
	t.Helper()
	f := &memorySidecarFake{token: "test-sidecar-token"}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *memorySidecarFake) port() int {
	u, err := url.Parse(f.srv.URL)
	if err != nil {
		panic(err)
	}
	p, err := strconv.Atoi(u.Port())
	if err != nil {
		panic(err)
	}
	return p
}

func (f *memorySidecarFake) handle(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != daemon.AuthScheme+" "+f.token {
		w.WriteHeader(http.StatusUnauthorized)
		writeJSON(w, map[string]any{"detail": "invalid client token"})
		return
	}
	switch {
	case r.URL.Path == daemon.APIPrefix+"/status":
		// StartOrAttach's two-check trust rule (client.go's verify/probe)
		// needs this before EnsureAgent will even try the ensure call below.
		writeJSON(w, map[string]any{"service": daemon.ServiceID, "pid": os.Getpid()})
	case strings.HasSuffix(r.URL.Path, "/agents/gaia/ensure"):
		writeJSON(w, map[string]any{"port": f.port(), "token": f.token, "state": "running"})
	case r.URL.Path == "/v1/gaia/version":
		// apiVersion 2.13 is memoryContractMajor/Minor (negotiate.go) -- the
		// version that introduced this very route.
		writeJSON(w, map[string]any{"apiVersion": "2.13", "version": "0.2.0"})
	case r.URL.Path == "/v1/gaia/memory":
		writeJSON(w, map[string]any{
			"available": true,
			"stats": map[string]any{
				"total_knowledge": 0, "by_category": map[string]any{}, "by_context": map[string]any{},
				"sensitive_count": 0, "entity_count": 0, "avg_confidence": 0,
			},
			"contexts": []any{}, "shown": 0, "total": 0, "items": []any{},
		})
	default:
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]any{"detail": "no route " + r.URL.Path})
	}
}

// pumpMemoryFetch drains the tea.Cmd chain a slash command produces --
// spinner ticks and the fetch's own Cmd -- feeding every resulting message
// back into Update, until nothing is left to run or the loop looks stuck.
func pumpMemoryFetch(t *testing.T, m chat.ChatModel, cmd tea.Cmd) chat.ChatModel {
	t.Helper()
	pending := []tea.Cmd{cmd}
	for steps := 0; len(pending) > 0; steps++ {
		if steps > maxPumpSteps {
			t.Fatalf("/memory's command chain did not settle after %d steps", maxPumpSteps)
		}
		c := pending[0]
		pending = pending[1:]
		if c == nil {
			continue
		}
		msg := c()
		if msg == nil {
			continue
		}
		if batch, ok := msg.(tea.BatchMsg); ok {
			pending = append(pending, batch...)
			continue
		}
		updated, next := m.Update(msg)
		m = updated.(chat.ChatModel)
		if next != nil {
			pending = append(pending, next)
		}
	}
	return m
}

// TestMemoryResolvesForARealHubInstalledFlagship is the #3978 acceptance
// regression: a real ".installed" sentinel (not catalog.SetMockBinary) flips
// the flagship to the daemon transport, and /memory must still resolve
// through it instead of hitting the stale "does not support /memory" refusal
// a bare transport-type assertion used to produce.
func TestMemoryResolvesForARealHubInstalledFlagship(t *testing.T) {
	home := t.TempDir()
	agentDir := filepath.Join(home, ".gaia", "agents", "gaia")
	if err := os.MkdirAll(agentDir, 0o755); err != nil {
		t.Fatal(err)
	}
	sentinel := `{"id":"gaia","version":"0.2.0","language":"python","artifact_kind":"binary"}`
	if err := os.WriteFile(filepath.Join(agentDir, catalog.SentinelName), []byte(sentinel), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	if runtime.GOOS == "windows" {
		t.Setenv("USERPROFILE", home)
	}

	c := catalog.NewCatalog()
	c.LoadInstalledAgents()
	agent := c.Get("gaia")
	if agent == nil {
		t.Fatal("gaia disappeared from the catalog after loading the sentinel")
	}
	if agent.Transport != catalog.TransportDaemon {
		t.Fatalf("transport = %v, want TransportDaemon -- the real bug scenario never set up", agent.Transport)
	}

	fake := newMemorySidecarFake(t)
	daemonHome := t.TempDir()
	t.Setenv(daemon.EnvHome, daemonHome)
	inst := daemon.Instance{
		PID: os.Getpid(), Port: fake.port(), Token: fake.token,
		Host: "127.0.0.1", APIVersion: "1.1", Service: daemon.ServiceID,
	}
	raw, err := json.Marshal(inst)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(daemonHome, "instance.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}

	cl, err := client.ForAgent(*agent, client.ForAgentOptions{})
	if err != nil {
		t.Fatalf("client.ForAgent: %v", err)
	}
	defer cl.Close()
	if _, ok := cl.(*client.SSEClient); !ok {
		t.Fatalf("built %T, want *client.SSEClient for a daemon-transport agent", cl)
	}

	// NewChatModelForFlagship, not NewChatModelForCatalogAgent -- root.model.go
	// builds the real launch this way (launchAgent), and setupVerified=true
	// is what the readiness gate already passing means: without it,
	// applyFirstBootGate arms setupChecking for the "gaia" agent id, and
	// runPaletteRow (palette.go) queues /memory behind that gate instead of
	// running it, so the fetch this test exists to prove never fires.
	m := chat.NewChatModelForFlagship(cl, agent.ID, agent.Name, agent.Version, false, true)
	updated, _ := m.Update(windowSize(100, 30))
	m = updated.(chat.ChatModel)

	// One rune per KeyMsg, the way the terminal actually delivers keystrokes
	// -- a single multi-rune KeyMsg would skip the per-keystroke palette sync
	// this command actually goes through (syncPalette, palette.go).
	for _, r := range "/memory" {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(chat.ChatModel)
	}
	updated, enterCmd := m.Update(keyEnter())
	m = updated.(chat.ChatModel)
	m = pumpMemoryFetch(t, m, enterCmd)

	view := m.View()
	if strings.Contains(view, "does not support /memory") ||
		strings.Contains(view, "not available over this connection") {
		t.Fatalf("/memory was refused for a real hub-installed flagship:\n%s", view)
	}
	if !strings.Contains(view, "No memories stored yet.") {
		t.Errorf("/memory did not render a resolved (if empty) memory view:\n%s", view)
	}
}
