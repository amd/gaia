// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gateway

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// hybridScreen is the models stage over an isolated config holding doc.
func hybridScreen(t *testing.T, doc map[string]any, models ...string) (GatewayModel, string) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("GAIA_GATEWAY_FILE", filepath.Join(dir, "gateway.json"))
	cfg := filepath.Join(dir, "config.json")
	t.Setenv("GAIA_CONFIG_FILE", cfg)
	if doc != nil {
		raw, err := json.Marshal(doc)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(cfg, raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	m := New(&Client{}, nil)
	remote := true
	m.hybrid = &hybridapi.Entry{ID: "hybrid", DisplayName: "Hybrid (DeepSeek + Opus)", Remote: &remote, Available: true}
	m.haveInit = true
	m.stage = stageModels
	m.width, m.height = 100, 40
	m.state.ActiveModel = "amd.Gemma-4-31B"
	for _, id := range models {
		m.models = append(m.models, Model{ID: id})
	}
	return m, cfg
}

func defaultModelIn(t *testing.T, cfg string) any {
	t.Helper()
	raw, err := os.ReadFile(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	return doc["default_model"]
}

func TestEnterOnHybridMakesItTheDefaultLikeGatewayUseHybrid(t *testing.T) {
	m, cfg := hybridScreen(t, map[string]any{"default_model": "amd.Gemma-4-31B", "full_access": true},
		"amd.DeepSeek-V4.1-Flash", "amd.Claude-Opus-5-5", "amd.Gemma-4-31B")
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "Hybrid (DeepSeek + Opus) (cloud)") || !strings.Contains(view, "amd.Gemma-4-31B (active)") {
		t.Fatalf("list:\n%s", view)
	}
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(GatewayModel)
	if m.errMsg != "" {
		t.Fatal(m.errMsg)
	}
	if got := defaultModelIn(t, cfg); got != "hybrid" {
		t.Fatalf("default_model = %v", got)
	}
	view = ansi.Strip(m.View())
	if !strings.Contains(view, "(cloud) (active)") || strings.Contains(view, "amd.Gemma-4-31B (active)") {
		t.Fatalf("hybrid not shown as the one in use:\n%s", view)
	}
	if m.state.ActiveModel != "amd.Gemma-4-31B" {
		t.Fatal("gateway use hybrid leaves the gateway's own active model alone")
	}
}

func TestHybridIsRefusedWithTheBackendsReason(t *testing.T) {
	m, cfg := hybridScreen(t, map[string]any{"default_model": "amd.Gemma-4-31B"},
		"amd.DeepSeek-V4.1-Flash", "amd.Claude-Opus-5-5")
	why := "The AMD LLM Gateway doesn't list amd.Tiny"
	m.hybrid.Available, m.hybrid.Reason = false, &why
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(GatewayModel)
	if !strings.Contains(m.errMsg, "amd.Tiny") {
		t.Fatalf("err %q", m.errMsg)
	}
	if got := defaultModelIn(t, cfg); got != "amd.Gemma-4-31B" {
		t.Fatalf("a refused hybrid still changed default_model to %v", got)
	}
}

func TestHybridIsNotChosenUntilTheBackendAnswers(t *testing.T) {
	m, cfg := hybridScreen(t, map[string]any{"default_model": "amd.Gemma-4-31B"},
		"amd.DeepSeek-V4.1-Flash", "amd.Claude-Opus-5-5")
	m.hybrid = nil
	if v := ansi.Strip(m.View()); !strings.Contains(v, "checking its settings") {
		t.Fatalf("list:\n%s", v)
	}
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if got := defaultModelIn(t, cfg); got != "amd.Gemma-4-31B" || next.(GatewayModel).errMsg == "" {
		t.Fatalf("hybrid chosen before the backend answered: %v", got)
	}
	next, _ = m.Update(hybridEntryMsg{err: &hybridapi.UnreachableError{What: "Naming hybrid", Reason: "isn't running"}})
	if v := ansi.Strip(next.(GatewayModel).View()); !strings.Contains(v, "needs GAIA's background service") {
		t.Fatalf("list:\n%s", v)
	}
}

func TestSpaceOnHybridTogglesNothing(t *testing.T) {
	m, _ := hybridScreen(t, nil, "amd.DeepSeek-V4.1-Flash", "amd.Claude-Opus-5-5")
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeySpace, Runes: []rune(" ")})
	m = next.(GatewayModel)
	if m.state.IsEnabled(lemonade.HybridModelID) || !strings.Contains(m.notice, "enter makes it the default") {
		t.Fatalf("notice %q", m.notice)
	}
}

// A user who chose hybrid with `gaia gateway use hybrid` and never picked a
// gateway model here must not have it replaced when the list loads.
func TestLoadingModelsNeverReplacesHybrid(t *testing.T) {
	m, cfg := hybridScreen(t, map[string]any{"default_model": "hybrid"})
	m.state.ActiveModel = ""
	next, _ := m.Update(modelsMsg{models: []Model{{ID: "amd.DeepSeek-V4.1-Flash"}, {ID: "amd.Claude-Opus-5-5"}}})
	m = next.(GatewayModel)
	if got := defaultModelIn(t, cfg); got != "hybrid" {
		t.Fatalf("default_model replaced with %v", got)
	}
}
