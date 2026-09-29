// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// keyStoreCalls records what the panel asked the daemon to keep, forget and
// replay. Every test in the package runs against it: none may reach a real
// daemon, let alone start one.
type keyStoreCalls struct {
	remembered  []string
	forgotten   []string
	restored    []string
	rememberErr error
}

var store keyStoreCalls

func TestMain(m *testing.M) {
	rememberKey = func(provider, key string) error {
		store.remembered = append(store.remembered, provider+"="+key)
		return store.rememberErr
	}
	forgetKey = func(provider string) error {
		store.forgotten = append(store.forgotten, provider)
		return nil
	}
	restoreKey = func(provider string) { store.restored = append(store.restored, provider) }
	os.Exit(m.Run())
}

func resetStore() { store = keyStoreCalls{} }

// A key that connected and discovered models is kept for later sessions.
func TestAWorkingKeyIsKept(t *testing.T) {
	resetStore()
	srv := fakeLemonade(t, []lemonade.Model{{ID: "fireworks.glm-5p3-flash", Recipe: "cloud", Provider: "fireworks"}})
	m := connectFireworks(t, srv, "fw-key")
	if len(store.remembered) != 1 || store.remembered[0] != "fireworks=fw-key" {
		t.Fatalf("kept %v, want the fireworks key", store.remembered)
	}
	if strings.Contains(m.View(), "this session only") {
		t.Error("a kept key must not be reported as session-only")
	}
}

// A key that discovers nothing is not kept: it would be replayed forever.
func TestAKeyThatFindsNoModelsIsNotKept(t *testing.T) {
	resetStore()
	srv := fakeLemonade(t, nil)
	connectFireworks(t, srv, "bad-key")
	if len(store.remembered) != 0 {
		t.Fatalf("kept %v for a key that discovered nothing", store.remembered)
	}
}

// When the credential store refuses, the user is told it works for now only.
func TestAKeyThatCannotBeKeptSaysSo(t *testing.T) {
	resetStore()
	store.rememberErr = errors.New("no credential store")
	srv := fakeLemonade(t, []lemonade.Model{{ID: "fireworks.glm-5p3-flash", Recipe: "cloud", Provider: "fireworks"}})
	m := connectFireworks(t, srv, "fw-key")
	if !strings.Contains(m.View(), "this session only") {
		t.Errorf("the panel must say the key was not kept:\n%s", m.View())
	}
}

// Opening the panel replays kept keys before reading provider state.
func TestOpeningThePanelRestoresKeptKeys(t *testing.T) {
	resetStore()
	srv := fakeLemonade(t, nil)
	m := New(srv, 100, 30)
	m.Init()()
	if strings.Join(store.restored, ",") != "fireworks,amd" {
		t.Errorf("restored %v, want fireworks and amd", store.restored)
	}
}

// Clearing forgets the kept copy too, or it would come back next launch.
func TestClearingForgetsTheKeptKey(t *testing.T) {
	resetStore()
	srv := fakeLemonade(t, nil)
	m := New(srv, 100, 30)
	m.selected = 1
	m = m.setup()
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlD})
	drain(cmd)
	if len(store.forgotten) != 1 || store.forgotten[0] != "fireworks" {
		t.Errorf("forgot %v, want fireworks", store.forgotten)
	}
}

// fakeLemonade serves the four routes the panel uses, on loopback.
func fakeLemonade(t *testing.T, models []lemonade.Model) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/models":
			_ = json.NewEncoder(w).Encode(map[string]any{"data": models})
		case "/api/v1/system-info":
			_, _ = w.Write([]byte(`{"cloud":{"providers":[]}}`))
		default: // /install, /cloud/auth
			_, _ = w.Write([]byte(`{}`))
		}
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/api/v1"
}

// drain runs a command and everything it batches, returning the messages that
// are not spinner or blink ticks.
func drain(cmd tea.Cmd) []tea.Msg {
	if cmd == nil {
		return nil
	}
	msg := cmd()
	if batch, ok := msg.(tea.BatchMsg); ok {
		var out []tea.Msg
		for _, c := range batch {
			out = append(out, drain(c)...)
		}
		return out
	}
	switch msg.(type) {
	case modelsMsg, clearedMsg, loadedMsg:
		return []tea.Msg{msg}
	}
	return nil
}

// connectFireworks types a key into the Fireworks setup and presses Enter.
func connectFireworks(t *testing.T, base, apiKey string) Model {
	t.Helper()
	m := New(base, 100, 30)
	m.selected = 1
	m = m.setup()
	m.fields[3].SetValue(apiKey)
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	for _, msg := range drain(cmd) {
		if _, ok := msg.(modelsMsg); ok {
			next, _ = m.Update(msg)
			m = next.(Model)
		}
	}
	return m
}
