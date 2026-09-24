// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package providers

import (
	"encoding/json"
	"errors"
	"fmt"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/charmbracelet/bubbles/cursor"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func key(m Model, k tea.KeyType) Model { next, _ := m.Update(tea.KeyMsg{Type: k}); return next.(Model) }

// plain wraps models as listed, selectable picker rows with no recommendations.
func plain(models ...lemonade.Model) []lemonade.Entry {
	out := make([]lemonade.Entry, len(models))
	for i, model := range models {
		out[i] = lemonade.Entry{Model: model, Listed: true, Fits: true}
	}
	return out
}
func TestMaskedPasteNeverReachesViewAndClearsOnCancel(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("fw-sensitive-test-key"), Paste: true})
	m = next.(Model)
	if m.fields[3].Value() != "fw-sensitive-test-key" {
		t.Fatal("paste did not reach credential field")
	}
	if strings.Contains(m.View(), "fw-sensitive-test-key") || !strings.Contains(m.View(), "•") {
		t.Fatal("password exposed or missing mask")
	}
	m = key(m, tea.KeyEsc)
	if m.fields != nil || m.stage != "providers" {
		t.Fatal("cancel retained key")
	}
}
func TestProviderScreenOffersAllDestinations(t *testing.T) {
	m := New("", 100, 30)
	for _, label := range []string{"Local", "Fireworks AI", "AMD LLM Gateway"} {
		if !strings.Contains(m.View(), label) {
			t.Fatal(label)
		}
	}
}
func TestSuggestedGemmaIsFirstAndSelectionIsExplicit(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	entries := lemonade.BuildEntries("fireworks", []lemonade.Model{{ID: "fireworks.z", Recipe: "cloud"}, {ID: lemonade.FireworksModel, Recipe: "cloud"}}, lemonade.Capacity{}, nil)
	next, _ := m.Update(modelsMsg{entries: entries})
	m = next.(Model)
	if m.ctx.Err() != nil || m.entries[0].Model.ID != lemonade.FireworksModel || m.stage != "models" {
		t.Fatal("model silently selected or suggestion missing")
	}
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd().(SelectedMsg).ID != lemonade.FireworksModel {
		t.Fatal("wrong selection")
	}
}
func TestEmptyCatalogStaysInSetup(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	m.busy = true
	next, _ := m.Update(modelsMsg{})
	m = next.(Model)
	if m.busy || m.stage != "setup" || !strings.Contains(m.View(), "No chat models discovered") {
		t.Fatal("empty discovery was treated as connected")
	}
}
func TestCredentialFieldRemainsVisibleInSmallTerminal(t *testing.T) {
	for _, size := range [][2]int{{80, 24}, {48, 18}, {32, 12}} {
		m := New("", size[0], size[1])
		m.selected = 1
		m = m.setup()
		view := m.View()
		if !strings.Contains(view, "API key") {
			t.Fatal("key field clipped", size)
		}
		for _, line := range strings.Split(view, "\n") {
			if ansi.StringWidth(line) > size[0] {
				t.Fatalf("overflow at %v: %q", size, line)
			}
		}
	}
}

func TestRefreshFailureOrEmptyCatalogCannotCrashSelection(t *testing.T) {
	for _, failure := range []bool{false, true} {
		m := New("", 80, 24)
		m.selected = 1
		m = m.setup()
		m.stage = "models"
		m.entries = plain(lemonade.Model{ID: lemonade.FireworksModel})
		result := modelsMsg{}
		if failure {
			result.err = errors.New("connection failed")
		}
		next, _ := m.Update(result)
		m = next.(Model)
		if failure && len(m.entries) != 1 {
			t.Fatal("failed refresh erased existing models")
		}
		if !failure && m.stage != "setup" {
			t.Fatal("empty refresh should return to setup")
		}
		m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	}
}

func TestModelSearchAndMetadata(t *testing.T) {
	m := New("", 80, 24)
	m.selected = 1
	next, _ := m.Update(modelsMsg{entries: plain(lemonade.Model{ID: lemonade.FireworksModel, ContextLength: 262144, Labels: []string{"tool-calling", "vision"}}, lemonade.Model{ID: "fireworks.qwen"})})
	m = next.(Model)
	next, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("gemma")})
	m = next.(Model)
	if len(m.filteredEntries()) != 1 || !strings.Contains(m.View(), "262,144 tokens") || !strings.Contains(m.View(), "tool calling") {
		t.Fatal("search or metadata missing", m.View())
	}
	next, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("no-match")})
	m = next.(Model)
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd != nil {
		t.Fatal("empty search selected a model")
	}
}
func TestConnectingCanBeCancelledWithoutWaiting(t *testing.T) {
	m := New("", 80, 24)
	m.selected = 1
	m = m.setup()
	m.busy = true
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = next.(Model)
	if m.ctx.Err() == nil || m.fields != nil {
		t.Fatal("cancel did not cancel request and clear input")
	}
	if _, ok := cmd().(ClosedMsg); !ok {
		t.Fatal("cancel did not close")
	}
}
func TestOldPanelResultsCannotAffectNewPanel(t *testing.T) {
	old := New("", 80, 24)
	m := New("", 80, 24)
	next, _ := m.Update(modelsMsg{source: old.client, entries: plain(lemonade.Model{ID: lemonade.FireworksModel})})
	m = next.(Model)
	if m.stage != "providers" {
		t.Fatal("late result reopened an old model selection")
	}
}

func TestCancelIgnoresQueuedInputAndBlinkEvents(t *testing.T) {
	for _, k := range []tea.KeyType{tea.KeyEsc, tea.KeyCtrlC} {
		m := New("", 80, 24)
		m.selected = 1
		m = m.setup()
		m.busy = true
		next, _ := m.Update(tea.KeyMsg{Type: k})
		m = next.(Model)
		for _, msg := range []tea.Msg{cursor.BlinkMsg{}, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("queued")}, tea.WindowSizeMsg{Width: 40, Height: 12}} {
			next, _ = m.Update(msg)
			m = next.(Model)
			m.View()
		}
	}
}

func TestNarrowProviderNoticeWrapsAtWords(t *testing.T) {
	m := New("", 48, 18)
	m.selected = 1
	m = m.setup()
	view := ansi.Strip(m.View())
	if strings.Contains(view, "restar\n") || strings.Contains(view, "of t\n") {
		t.Fatal("notice splits words", view)
	}
	if len(strings.Split(view, "\n")) > 18 {
		t.Fatal("panel exceeds terminal height", view)
	}
}

func TestSetupNoticesExistingEnvironmentKey(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	m.providers = []lemonade.Provider{{Name: "fireworks", EnvKey: true}}
	m = m.setup()
	view := m.View()
	if !strings.Contains(view, "environment key is already active") {
		t.Fatal("existing environment key was not surfaced to the user", view)
	}
	if !strings.Contains(m.fields[3].Placeholder, "environment key") {
		t.Fatal("key field placeholder does not mention the environment key", m.fields[3].Placeholder)
	}
}

func TestSetupNoticesExistingRuntimeKey(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 2
	m.providers = []lemonade.Provider{{Name: "amd", BaseURL: "https://gw.example.com", RuntimeKey: true}}
	m = m.setup()
	view := m.View()
	if !strings.Contains(view, "already configured for AMD LLM Gateway") {
		t.Fatal("existing runtime key was not surfaced to the user", view)
	}
	if strings.Contains(view, "environment key is already active") {
		t.Fatal("runtime-only key incorrectly reported as an environment key", view)
	}
}

func TestSetupNoticeIsShortOnCompactTerminal(t *testing.T) {
	m := New("", 48, 18)
	m.selected = 2
	m.providers = []lemonade.Provider{{Name: "amd", BaseURL: "https://gw.example.com", RuntimeKey: true}}
	m = m.setup()
	view := m.View()
	if !strings.Contains(view, "Key already saved") {
		t.Fatal("compact terminal should get the short notice variant", view)
	}
	if strings.Contains(view, "leave API key blank to keep using it, or paste a new one to replace it") {
		t.Fatal("compact terminal should not get the long notice variant", view)
	}
}

func TestSetupWithNoExistingKeyShowsNoNotice(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	// A present provider with both flags false is the state a real first-run
	// user is in — not a missing provider entry (see keyStatus's fallback).
	m.providers = []lemonade.Provider{{Name: "fireworks"}}
	m = m.setup()
	view := m.View()
	if strings.Contains(view, "already active") || strings.Contains(view, "already configured for") {
		t.Fatal("notice shown despite no existing credential", view)
	}
	if m.fields[3].Placeholder != "Paste API key" {
		t.Fatalf("placeholder should be plain with no existing key, got %q", m.fields[3].Placeholder)
	}
}

// Regression: the notice used to be snapshotted once in setup() and never
// re-derived, so it kept claiming a key was active after Ctrl+D cleared it.
func TestClearingRuntimeKeyDropsTheExistingKeyNotice(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 2
	m.providers = []lemonade.Provider{{Name: "amd", BaseURL: "https://gw.example.com", RuntimeKey: true}}
	m = m.setup()
	if !strings.Contains(m.View(), "already configured for AMD LLM Gateway") {
		t.Fatal("precondition: notice should show before clearing")
	}
	next, _ := m.Update(clearedMsg{})
	m = next.(Model)
	view := m.View()
	if strings.Contains(view, "already configured for") || strings.Contains(view, "already active") {
		t.Fatal("stale notice still claims a key is active after it was cleared", view)
	}
	if !strings.Contains(view, "Runtime key cleared") {
		t.Fatal("missing clear confirmation", view)
	}
}

func TestCompactGatewayKeepsProviderAndFieldsVisible(t *testing.T) {
	m := New("", 48, 18)
	m.selected = 2
	m.providers = []lemonade.Provider{{Name: "amd", BaseURL: "https://gw.example.com", RuntimeKey: true}}
	m = m.setup()
	for _, label := range []string{"AMD LLM Gateway", "Gateway URL", "Auth header", "API key", "esc back"} {
		if !strings.Contains(m.View(), label) {
			t.Fatalf("compact gateway with an existing key lost %s: %s", label, m.View())
		}
	}
}

// localPicker opens the local model list against a stub Lemonade at url.
func localPicker(url string, capacity lemonade.Capacity, models ...lemonade.Model) Model {
	m := New(url, 100, 40)
	next, _ := m.Update(modelsMsg{source: m.client, entries: lemonade.BuildEntries("local", models, capacity, nil), capacity: "This PC: 12 GB for models (Apple GPU)"})
	return next.(Model)
}

var smallMac = lemonade.Capacity{MemoryGB: 12, MemorySource: "Apple GPU", DiskFreeGB: 20}

func TestTooBigModelIsShownButCannotBeDownloaded(t *testing.T) {
	m := localPicker("", smallMac, lemonade.Model{ID: "Gemma-4-E4B-it-GGUF", Downloaded: true, Size: 5.97, Labels: []string{"chat"}})
	view := m.View()
	for _, want := range []string{"Recommended", "★ Qwen3.8 Flash Next", "won't fit", "This PC: 12 GB"} {
		if !strings.Contains(view, want) {
			t.Fatalf("view lacks %q:\n%s", want, view)
		}
	}
	if m.entries[0].Model.ID != "Qwen3.8-Flash-Next-GGUF" {
		t.Fatalf("first row %s", m.entries[0].Model.ID)
	}
	if m.entries[m.focus].Model.ID != "Gemma-4-E4B-it-GGUF" {
		t.Fatalf("cursor should start on the first usable row, got %s", m.entries[m.focus].Model.ID)
	}
	m.focus = 0
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	if cmd != nil || m.stage != "models" || !strings.Contains(m.note, "memory") {
		t.Fatalf("a model that does not fit started something: stage=%s note=%q", m.stage, m.note)
	}
	next, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("g")})
	if next.(Model).note != "" {
		t.Fatal("a refusal note outlived the search that replaced it")
	}
}

func TestFittingModelDownloadsThenIsSelected(t *testing.T) {
	var pulled string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		pulled, _ = body["model_name"].(string)
		fmt.Fprint(w, "event: progress\ndata: {\"percent\":50}\n\nevent: complete\ndata: {}\n\n")
	}))
	defer s.Close()
	m := localPicker(s.URL, smallMac, lemonade.Model{ID: "Tiny-GGUF", Size: 1, Labels: []string{"chat"}})
	for i, e := range m.filteredEntries() {
		if e.Model.ID == "Tiny-GGUF" {
			m.focus = i
		}
	}
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	if m.stage != "download" || cmd == nil {
		t.Fatalf("stage=%s", m.stage)
	}
	// Feed each command's message back in, as Bubble Tea would, until the
	// panel selects (the spinner tick in the first batch is skipped).
	var selected string
	msg := waitPull(m.pullCh)()
	for i := 0; i < 20 && msg != nil && selected == ""; i++ {
		if sel, ok := msg.(SelectedMsg); ok {
			selected = sel.ID
			break
		}
		next, cmd = m.Update(msg)
		m = next.(Model)
		if cmd == nil {
			break
		}
		msg = cmd()
	}
	if selected != "Tiny-GGUF" || pulled != "Tiny-GGUF" {
		t.Fatalf("selected=%q pulled=%q note=%q", selected, pulled, m.note)
	}
}

func TestEscStopsADownloadAndReturnsToTheList(t *testing.T) {
	block := make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.(http.Flusher).Flush()
		select {
		case <-block:
		case <-r.Context().Done():
		}
	}))
	defer s.Close()
	defer close(block)
	m := localPicker(s.URL, smallMac, lemonade.Model{ID: "Tiny-GGUF", Size: 1, Labels: []string{"chat"}})
	for i, e := range m.filteredEntries() {
		if e.Model.ID == "Tiny-GGUF" {
			m.focus = i
		}
	}
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = key(next.(Model), tea.KeyEsc)
	if m.stage != "models" || m.ctx.Err() != nil || !strings.Contains(m.note, "stopped") {
		t.Fatalf("stage=%s note=%q panelCtx=%v", m.stage, m.note, m.ctx.Err())
	}
	// The late completion must not select anything.
	next, cmd := m.Update(pullDoneMsg{source: m.client, id: "Tiny-GGUF"})
	if cmd != nil || next.(Model).stage != "models" {
		t.Fatal("a stopped download still selected its model")
	}
}
