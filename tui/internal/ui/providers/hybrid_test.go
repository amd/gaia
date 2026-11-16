// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/lemonade"
)

var defaultHybrid = lemonade.HybridModels{
	Cheap: lemonade.DefaultHybridCheapModel, Strong: lemonade.DefaultHybridStrongModel,
	Classifier: lemonade.DefaultHybridCheapModel,
}

func stubHybrid(t *testing.T, h lemonade.HybridModels, readErr, localErr error) {
	t.Helper()
	origRead, origLocal := readHybridModels, hybridLocalRefuse
	t.Cleanup(func() { readHybridModels, hybridLocalRefuse = origRead, origLocal })
	readHybridModels = func() (lemonade.HybridModels, error) { return h, readErr }
	hybridLocalRefuse = func() error { return localErr }
}

func gatewayEntries(ids ...string) []lemonade.Entry {
	models := make([]lemonade.Model, len(ids))
	for i, id := range ids {
		models[i] = lemonade.Model{ID: id, Recipe: "cloud"}
	}
	return plain(models...)
}

func TestGatewayListLeadsWithHybrid(t *testing.T) {
	stubHybrid(t, defaultHybrid, nil, nil)
	entries := withHybrid(gatewayEntries(lemonade.DefaultHybridCheapModel, lemonade.DefaultHybridStrongModel))
	if len(entries) != 3 || entries[0].Model.ID != lemonade.HybridModelID {
		t.Fatalf("entries %s", ids(entries))
	}
	h := entries[0]
	if !h.Selectable() || h.NeedsDownload() {
		t.Fatalf("hybrid must be choosable and never downloaded: %+v", h)
	}
	if got := entryName(h, "amd"); got != "Hybrid (DeepSeek + Opus) (cloud)" {
		t.Fatalf("label %q", got)
	}
	if entryGroup(h) != "Hybrid routing" {
		t.Fatalf("group %q", entryGroup(h))
	}
	if len(withHybrid(nil)) != 0 {
		t.Fatal("hybrid offered with nothing on the gateway to route between")
	}
}

func TestHybridRowSaysWhyItCannotBeChosen(t *testing.T) {
	for name, tc := range map[string]struct {
		h         lemonade.HybridModels
		readErr   error
		localErr  error
		gateway   []string
		wantInWhy string
	}{
		"missing strong": {defaultHybrid, nil, nil, []string{lemonade.DefaultHybridCheapModel}, "doesn't list amd.Claude-Opus-5-5, hybrid's strong model"},
		"broken config":  {lemonade.HybridModels{}, errors.New("hybrid_policy must be one of quality"), nil, []string{"amd.x"}, "Fix it in /hybrid"},
		"local only":     {defaultHybrid, nil, errors.New("Local only is on, so hybrid won't send turns to amd.x"), []string{lemonade.DefaultHybridCheapModel, lemonade.DefaultHybridStrongModel}, "Local only is on"},
	} {
		t.Run(name, func(t *testing.T) {
			stubHybrid(t, tc.h, tc.readErr, tc.localErr)
			h := withHybrid(gatewayEntries(tc.gateway...))[0]
			if h.Selectable() || !strings.Contains(h.Reason, tc.wantInWhy) {
				t.Fatalf("selectable=%v reason=%q", h.Selectable(), h.Reason)
			}
		})
	}
}

// A model hybrid routes to that is not a gateway model (a local one) is checked
// when the switch is made, not refused here.
func TestNonGatewayMembersAreLeftToTheSwitchCheck(t *testing.T) {
	stubHybrid(t, lemonade.HybridModels{Cheap: "Gemma-4-E4B-it-GGUF", Strong: lemonade.DefaultHybridStrongModel,
		Classifier: "Gemma-4-E4B-it-GGUF"}, nil, nil)
	h := withHybrid(gatewayEntries(lemonade.DefaultHybridStrongModel))[0]
	if !h.Selectable() {
		t.Fatalf("refused: %s", h.Reason)
	}
}

func TestChoosingHybridSelectsTheHybridID(t *testing.T) {
	stubHybrid(t, defaultHybrid, nil, nil)
	m := New("", 100, 40)
	m.selected = 2 // amd
	m = m.setup()
	next, _ := m.Update(modelsMsg{source: m.client,
		entries: withHybrid(gatewayEntries(lemonade.DefaultHybridCheapModel, lemonade.DefaultHybridStrongModel))})
	m = next.(Model)
	lines := strings.Split(ansi.Strip(m.View()), "\n")
	for i := range lines {
		lines[i] = strings.TrimSpace(lines[i])
	}
	view := strings.Join(lines, "")
	if strings.Contains(view, "★ Hybrid") {
		t.Fatalf("hybrid is not a recommended model:\n%s", view)
	}
	if !strings.Contains(view, "Hybrid routing") || !strings.Contains(view, "Hybrid (DeepSeek + Opus) (cloud)") {
		t.Fatalf("hybrid not listed:\n%s", view)
	}
	if !strings.Contains(view, "picked by "+lemonade.DefaultHybridCheapModel) {
		t.Fatalf("the focused hybrid row does not say what it routes between:\n%s", view)
	}
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if got := cmd().(SelectedMsg).ID; got != lemonade.HybridModelID {
		t.Fatalf("selected %q", got)
	}
}
