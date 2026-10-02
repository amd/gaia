// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/lemonade"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// deadLemonade is a loopback address nothing listens on — a machine where
// Lemonade is not installed yet.
func deadLemonade(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.NotFoundHandler())
	base := srv.URL + "/api/v1"
	srv.Close()
	return base
}

// flat joins wrapped lines so a sentence can be matched whole.
func flat(m Model) string { return strings.Join(strings.Fields(ansi.Strip(m.View())), " ") }

func loaded(t *testing.T, m Model) Model {
	t.Helper()
	for _, msg := range drain(m.Init()) {
		next, _ := m.Update(msg)
		m = next.(Model)
	}
	return m
}

// Before first-run setup installs Lemonade, the picker says which step comes
// first instead of reporting a Lemonade that "did not respond".
func TestPickerBeforeLemonadeIsInstalledNamesTheStep(t *testing.T) {
	resetStore()
	m := loaded(t, New(deadLemonade(t), 100, 30).WithSetupStep(1))
	view := flat(m)
	for _, want := range []string{
		"Lemonade isn't installed yet — setup step 1 installs it.",
		"Available after setup step 1",
		"r re-check · esc close",
	} {
		if !strings.Contains(view, want) {
			t.Errorf("missing %q in:\n%s", want, view)
		}
	}
	for _, fault := range []string{"did not respond", "check its address", "key needed"} {
		if strings.Contains(view, fault) {
			t.Errorf("a step not reached rendered as a fault (%q):\n%s", fault, view)
		}
	}
	m.selected = 1
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if m = next.(Model); m.stage != "providers" || cmd != nil {
		t.Errorf("enter opened %q with nothing to connect to", m.stage)
	}
}

// Past setup, an unreachable Lemonade is a real fault and still says so.
func TestPickerWithLemonadeDownAfterSetupReportsTheFault(t *testing.T) {
	resetStore()
	view := flat(loaded(t, New(deadLemonade(t), 100, 30)))
	if !strings.Contains(view, "Lemonade did not respond. Start it, check its address, and retry") {
		t.Errorf("fault not reported:\n%s", view)
	}
	if strings.Contains(view, "setup step") {
		t.Errorf("a finished setup is still named as a step:\n%s", view)
	}
}

// registeringLemonade lists Fireworks only once it is registered, as Lemonade
// does, and reports the key it was started with (LEMONADE_FIREWORKS_API_KEY).
func registeringLemonade(t *testing.T) (base string, paths func() []string) {
	t.Helper()
	var mu sync.Mutex
	var seen []string
	registered := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		seen = append(seen, r.Method+" "+strings.TrimPrefix(r.URL.Path, "/api/v1"))
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/install":
			registered = true
			_, _ = w.Write([]byte(`{}`))
		case "/api/v1/system-info":
			if !registered {
				_, _ = w.Write([]byte(`{"cloud":{"providers":[]}}`))
				return
			}
			_, _ = w.Write([]byte(`{"cloud":{"providers":[{"name":"fireworks",` +
				`"base_url":"https://api.fireworks.ai/inference/v1","auth_header_name":"Authorization",` +
				`"auth_header_prefix":"Bearer ","env_var_set":true,"runtime_key_set":false}]}}`))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/api/v1", func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), seen...)
	}
}

// A key Lemonade already holds is shown as set, not asked for again.
func TestKeyFieldSaysAKeyIsSetWhenLemonadeHoldsOne(t *testing.T) {
	resetStore()
	base, paths := registeringLemonade(t)
	m := loaded(t, New(base, 100, 30))
	m.selected = 1
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	for _, msg := range drain(cmd) {
		next, _ = m.Update(msg)
		m = next.(Model)
	}
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "A key is already set — Enter connects") {
		t.Errorf("key field does not say a key is set:\n%s", view)
	}
	if strings.Contains(view, "Paste API key") {
		t.Errorf("key field still asks for a key Lemonade holds:\n%s", view)
	}
	for _, p := range paths() {
		if strings.Contains(p, "/cloud/auth") {
			t.Errorf("opening setup sent a key: %v", paths())
		}
	}
	// A kept key can only go back into a registered provider, so it is retried.
	if n := len(store.restored); n == 0 || store.restored[n-1] != "fireworks" {
		t.Errorf("kept key not restored after registering: %v", store.restored)
	}
}

// A registration that fails keeps the keys already known.
func TestFailedRegistrationKeepsKnownProviders(t *testing.T) {
	resetStore()
	m := New(deadLemonade(t), 100, 30)
	m.providers = []lemonade.Provider{{Name: "amd", BaseURL: "https://gw.example.com", RuntimeKey: true}}
	m.selected = 1
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	for _, msg := range drain(cmd) {
		next, _ = m.Update(msg)
		m = next.(Model)
	}
	m = key(m, tea.KeyEsc)
	if view := flat(m); !strings.Contains(view, "AMD LLM Gateway Via Lemonade · key configured") {
		t.Errorf("failed registration forgot the AMD key:\n%s", view)
	}
}

func TestKeyFieldAsksForAKeyWhenNoneIsSet(t *testing.T) {
	resetStore()
	m := New("", 100, 30)
	m.selected = 1
	if view := ansi.Strip(m.setup().View()); !strings.Contains(view, "Paste API key") {
		t.Errorf("empty key field does not ask for one:\n%s", view)
	}
}

// Since keys are kept across restarts, the setup text says where, at both heights.
func TestSetupSaysWhereAPastedKeyIsKept(t *testing.T) {
	for height, want := range map[int]string{
		30: "A pasted key is saved in this computer's credential store and handed back to Lemonade after it restarts.",
		20: "Remote chat · key saved on this computer.",
	} {
		m := New("", 100, height)
		m.selected = 1
		view := flat(m.setup())
		if !strings.Contains(view, want) {
			t.Errorf("height %d: missing %q in:\n%s", height, want, view)
		}
		for _, stale := range []string{"Lemonade memory", "until restart"} {
			if strings.Contains(view, stale) {
				t.Errorf("height %d: stale %q in:\n%s", height, stale, view)
			}
		}
	}
}

// A slow read sent before registration cannot undo the key registration revealed.
func TestAnOlderProviderReadCannotUndoANewerOne(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	sent := time.Now()
	fresh := loadedMsg{started: sent, providers: []lemonade.Provider{{Name: "fireworks", EnvKey: true}}}
	stale := loadedMsg{started: sent.Add(-time.Second)}
	for _, msg := range []loadedMsg{fresh, stale} {
		next, _ := m.Update(msg)
		m = next.(Model)
	}
	if view := flat(m); !strings.Contains(view, "A key is already set — Enter connects") {
		t.Errorf("a stale read put the key prompt back:\n%s", view)
	}
}
