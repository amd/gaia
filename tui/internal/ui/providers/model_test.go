// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package providers

import (
	"errors"
	"fmt"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/charmbracelet/bubbles/cursor"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
	"strings"
	"testing"
)

func key(m Model, k tea.KeyType) Model { next, _ := m.Update(tea.KeyMsg{Type: k}); return next.(Model) }
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
func ids(models []lemonade.Model) string {
	var out []string
	for _, m := range models {
		out = append(out, m.ID)
	}
	return strings.Join(out, ",")
}
func TestRecommendedModelsLeadInRankOrderAndSelectionIsExplicit(t *testing.T) {
	rec := lemonade.RecommendedModels
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: "fireworks.aaa"}, {ID: rec[2].ID}, {ID: "fireworks.zzz"}, {ID: rec[0].ID}, {ID: rec[1].ID}}})
	m = next.(Model)
	want := strings.Join([]string{rec[0].ID, rec[1].ID, rec[2].ID, "fireworks.aaa", "fireworks.zzz"}, ",")
	if m.ctx.Err() != nil || m.stage != "models" || ids(m.models) != want {
		t.Fatalf("model silently selected or order wrong: %s", ids(m.models))
	}
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd().(SelectedMsg).ID != lemonade.TopRecommendation().ID {
		t.Fatal("wrong selection")
	}
}
func TestMissingRecommendedModelIsNeverInjected(t *testing.T) {
	rec := lemonade.RecommendedModels
	m := New("", 100, 30)
	m.selected = 1
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: "fireworks.b"}, {ID: rec[2].ID}, {ID: "fireworks.a"}}})
	m = next.(Model)
	if ids(m.models) != strings.Join([]string{rec[2].ID, "fireworks.a", "fireworks.b"}, ",") {
		t.Fatalf("absent recommendation broke ordering: %s", ids(m.models))
	}
	if strings.Contains(m.View(), strings.TrimPrefix(rec[0].ID, "fireworks.")) {
		t.Fatal("top recommendation shown although the provider does not serve it")
	}
}
func TestRecommendedRowsShowRankAndNoteOnOneLine(t *testing.T) {
	rec := lemonade.RecommendedModels
	for _, width := range []int{100, 48} {
		m := New("", width, 30)
		m.selected = 1
		next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: "fireworks.plain-model"}, {ID: rec[1].ID}, {ID: rec[0].ID}}})
		m = next.(Model)
		view := ansi.Strip(m.View())
		for i, r := range rec[:2] {
			row := fmt.Sprintf("%s · #%d %s", strings.TrimPrefix(r.ID, "fireworks."), i+1, r.Note)
			if width == 100 && !strings.Contains(view, row) {
				t.Fatalf("missing %q in %s", row, view)
			}
			if strings.Count(view, fmt.Sprintf("#%d ", i+1)) != 1 {
				t.Fatalf("rank %d rendered on %d lines at width %d: %s", i+1, strings.Count(view, fmt.Sprintf("#%d ", i+1)), width, view)
			}
		}
		for _, line := range strings.Split(view, "\n") {
			if strings.Contains(line, "plain-model") && strings.Contains(line, "#") {
				t.Fatalf("plain row carries a rank: %q", line)
			}
			if ansi.StringWidth(line) > width {
				t.Fatalf("row overflows width %d: %q", width, line)
			}
		}
	}
}
// The widths above are all wide enough to fit a ranked row untruncated, so they
// never exercise the guard. This one is not: without the truncate the row wraps
// onto a second line and pushes a row above it out of the height budget.
func TestNarrowRankedRowStaysOnOneTruncatedLine(t *testing.T) {
	top := lemonade.TopRecommendation()
	name := strings.TrimPrefix(top.ID, "fireworks.")
	width := 36
	if got := len(name) + len(" · #1 ") + len(top.Note) + 2; got <= width-4 {
		t.Fatalf("width %d no longer forces a truncation (row is %d cells); lower it", width, got)
	}
	m := New("", width, 30)
	m.selected = 1
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: top.ID}}})
	m = next.(Model)

	var rows []string
	for _, line := range strings.Split(ansi.Strip(m.View()), "\n") {
		if strings.Contains(line, name) {
			rows = append(rows, strings.TrimRight(line, " "))
		}
	}
	if len(rows) != 1 {
		t.Fatalf("ranked row rendered on %d lines at width %d: %q", len(rows), width, rows)
	}
	if !strings.HasSuffix(rows[0], "…") {
		t.Fatalf("row was not truncated at width %d: %q", width, rows[0])
	}
	if strings.Contains(ansi.Strip(m.View()), top.Note) {
		t.Fatalf("note survived intact at width %d, so nothing was truncated: %q", width, rows[0])
	}
}

func TestSetupScreenNamesTopRecommendation(t *testing.T) {
	top := lemonade.TopRecommendation()
	m := New("", 100, 30)
	m.selected = 1
	m = m.setup()
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "Recommended model: "+strings.TrimPrefix(top.ID, "fireworks.")+" · "+top.Note) {
		t.Fatalf("setup screen does not name %s: %s", top.ID, view)
	}
	if strings.Contains(view, "Gemma 4 31B") {
		t.Fatal("setup screen still names a model the provider no longer serves")
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
		m.models = []lemonade.Model{{ID: "fireworks.any"}}
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
	next, _ := m.Update(modelsMsg{models: []lemonade.Model{{ID: "fireworks.gemma", ContextLength: 262144, Labels: []string{"tool-calling", "vision"}}, {ID: "fireworks.qwen"}}})
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
	next, _ := m.Update(modelsMsg{source: old.client, models: []lemonade.Model{{ID: "fireworks.any"}}})
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
