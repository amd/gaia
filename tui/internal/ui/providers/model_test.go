// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package providers

import (
	"errors"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/charmbracelet/bubbles/cursor"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
	"strings"
	"testing"
)

func key(m Model, k tea.KeyType) Model { next, _ := m.Update(tea.KeyMsg{Type: k}); return next.(Model) }
func pick(name string) int {
	for i, n := range names {
		if n == name {
			return i
		}
	}
	panic(name)
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
	for _, label := range []string{"Local", "Fireworks AI", "QwenCloud", "AMD LLM Gateway"} {
		if !strings.Contains(m.View(), label) {
			t.Fatal(label)
		}
	}
}
func TestSuggestedGemmaIsFirstAndSelectionIsExplicit(t *testing.T) {
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: "fireworks.z"}, {ID: lemonade.FireworksModel}}})
	m = next.(Model)
	if m.ctx.Err() != nil || m.models[0].ID != lemonade.FireworksModel || m.stage != "models" {
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
		m.models = []lemonade.Model{{ID: lemonade.FireworksModel}}
		result := modelsMsg{}
		if failure {
			result.err = errors.New("connection failed")
		}
		next, _ := m.Update(result)
		m = next.(Model)
		if failure && len(m.models) != 1 {
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
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: lemonade.FireworksModel, ContextLength: 262144, Labels: []string{"tool-calling", "vision"}}, {ID: "fireworks.qwen"}}})
	m = next.(Model)
	next, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("gemma")})
	m = next.(Model)
	if len(m.filteredModels()) != 1 || !strings.Contains(m.View(), "262,144 tokens") || !strings.Contains(m.View(), "tool calling") {
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
	next, _ := m.Update(modelsMsg{source: old.client, models: []lemonade.Model{{ID: lemonade.FireworksModel}}})
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
	m.selected = pick("amd")
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
	m.selected = pick("amd")
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
	m.selected = pick("amd")
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
	m.selected = pick("amd")
	m.providers = []lemonade.Provider{{Name: "amd", BaseURL: "https://gw.example.com", RuntimeKey: true}}
	m = m.setup()
	for _, label := range []string{"AMD LLM Gateway", "Gateway URL", "Auth header", "API key", "esc back"} {
		if !strings.Contains(m.View(), label) {
			t.Fatalf("compact gateway with an existing key lost %s: %s", label, m.View())
		}
	}
}

func TestQwenCloudSitsBesideFireworksWithAFixedEndpoint(t *testing.T) {
	if pick("qwencloud") != pick("fireworks")+1 {
		t.Fatal("QwenCloud should be listed next to Fireworks")
	}
	m := New("", 100, 30)
	m.selected = pick("qwencloud")
	// Another client pointed the provider elsewhere; setup must not reuse it.
	m.providers = []lemonade.Provider{{Name: "qwencloud", BaseURL: "https://elsewhere.example/v1", Header: "X-Key"}}
	m = m.setup()
	if m.fields[0].Value() != lemonade.QwenCloudURL || m.fields[1].Value() != "Authorization" || m.focus != 3 {
		t.Fatalf("endpoint not pinned: %q %q focus=%d", m.fields[0].Value(), m.fields[1].Value(), m.focus)
	}
	view := m.View()
	for _, want := range []string{"Chat history is sent to QwenCloud", "pay-as-you-go", lemonade.QwenCloudURL} {
		if !strings.Contains(view, want) {
			t.Fatalf("setup view missing %q:\n%s", want, view)
		}
	}
	if strings.Contains(view, "Gateway URL") {
		t.Fatal("fixed-endpoint provider should not offer a URL field")
	}
	m = key(m, tea.KeyTab)
	if m.focus != 3 {
		t.Fatal("tab left the key field of a fixed-endpoint provider")
	}
}

func TestQwenCloudSuggestsQwenMaxFirst(t *testing.T) {
	m := New("", 100, 30)
	m.selected = pick("qwencloud")
	m = m.setup()
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: "qwencloud.a"}, {ID: lemonade.QwenCloudModel}}})
	m = next.(Model)
	if m.models[0].ID != lemonade.QwenCloudModel || !strings.Contains(m.View(), "qwen3.8-max · suggested") {
		t.Fatalf("suggestion not first:\n%s", m.View())
	}
}

func TestProviderCursorWrapsOverEveryDestination(t *testing.T) {
	m := New("", 100, 30)
	m = key(m, tea.KeyUp)
	if m.chosen() != "amd" {
		t.Fatalf("up from the top should wrap to the last provider, got %s", m.chosen())
	}
	for range names {
		m = key(m, tea.KeyDown)
	}
	if m.chosen() != "amd" {
		t.Fatalf("a full lap should return to the start, got %s", m.chosen())
	}
}

func TestCompactQwenCloudSetupStillNamesTheKeyType(t *testing.T) {
	m := New("", 48, 18)
	m.selected = pick("qwencloud")
	m.providers = []lemonade.Provider{{Name: "qwencloud", RuntimeKey: true}}
	m = m.setup()
	if !strings.Contains(m.View(), "Pay-as-you-go key") {
		t.Fatalf("compact QwenCloud setup lost the key type:\n%s", m.View())
	}
}
