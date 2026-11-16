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

func stubHybrid(t *testing.T, h lemonade.HybridModels, readErr, localErr error) {
	t.Helper()
	origRead, origLocal := readHybridModels, hybridLocalRefuse
	t.Cleanup(func() { readHybridModels, hybridLocalRefuse = origRead, origLocal })
	readHybridModels = func() (lemonade.HybridModels, error) { return h, readErr }
	hybridLocalRefuse = func() error { return localErr }
}

// joined is the screen with wrapped lines rejoined.
func joined(m Model) string {
	lines := strings.Split(ansi.Strip(m.View()), "\n")
	for i := range lines {
		lines[i] = strings.TrimSpace(lines[i])
	}
	return strings.Join(lines, " ")
}

func hybridPanel(t *testing.T) Model {
	t.Helper()
	m := New("", 100, 40).WithCurrent(lemonade.HybridProvider)
	if m.chosen() != lemonade.HybridProvider {
		t.Fatalf("panel did not open on hybrid: %s", m.chosen())
	}
	return m
}

func TestHybridIsItsOwnRowNamedByItsPair(t *testing.T) {
	for _, tc := range []struct {
		h    lemonade.HybridModels
		want string
	}{
		{lemonade.HybridModels{Cheap: "amd.DeepSeek-V4.1-Flash", Strong: "amd.Claude-Opus-5-5", Classifier: "amd.DeepSeek-V4.1-Flash"},
			"Hybrid (DeepSeek + Opus) (cloud)"},
		{lemonade.HybridModels{Cheap: "Gemma-4-E4B-it-GGUF", Strong: "Qwen3.6-35B-A3B-GGUF", Classifier: "Gemma-4-E4B-it-GGUF"},
			"Hybrid (Gemma + Qwen) (local)"},
		{lemonade.HybridModels{Cheap: "fireworks.deepseek-v4p1-flash", Strong: "claude-opus-5", Classifier: "fireworks.deepseek-v4p1-flash"},
			"Hybrid (DeepSeek + Opus) (cloud)"},
	} {
		stubHybrid(t, tc.h, nil, nil)
		v := joined(hybridPanel(t))
		if !strings.Contains(v, "› "+tc.want) || !strings.Contains(v, "/hybrid changes the models") {
			t.Errorf("want %q:\n%s", tc.want, v)
		}
	}
}

func TestChoosingHybridSelectsTheHybridID(t *testing.T) {
	stubHybrid(t, lemonade.HybridModels{Cheap: "Gemma-4-E4B-it-GGUF", Strong: "amd.Claude-Opus-5-5",
		Classifier: "Gemma-4-E4B-it-GGUF"}, nil, nil)
	_, cmd := hybridPanel(t).Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd == nil {
		t.Fatal("enter on hybrid did nothing")
	}
	if got := cmd().(SelectedMsg).ID; got != lemonade.HybridModelID {
		t.Fatalf("selected %q", got)
	}
}

func TestHybridRowSaysWhyItCannotBeChosen(t *testing.T) {
	for name, tc := range map[string]struct {
		readErr, localErr error
		want              string
	}{
		"broken config": {errors.New("hybrid_policy must be one of quality"), nil, "Fix it in /hybrid"},
		"local only":    {nil, errors.New("Local only is on, so hybrid won't send turns to amd.x"), "Local only is on"},
	} {
		t.Run(name, func(t *testing.T) {
			stubHybrid(t, lemonade.HybridModels{Cheap: "amd.a", Strong: "amd.b", Classifier: "amd.a"}, tc.readErr, tc.localErr)
			m := hybridPanel(t)
			if v := joined(m); !strings.Contains(v, "Can't use: ") || !strings.Contains(v, tc.want) {
				t.Fatalf("row:\n%s", v)
			}
			next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
			if cmd != nil {
				t.Fatal("an unusable hybrid was selected")
			}
			if !strings.Contains(next.(Model).note, tc.want) {
				t.Fatalf("note %q", next.(Model).note)
			}
		})
	}
}

// Hybrid is not one of the gateway's models any more: its own row replaces it.
func TestTheGatewayListHasNoHybridRow(t *testing.T) {
	stubHybrid(t, lemonade.HybridModels{Cheap: "amd.a", Strong: "amd.b", Classifier: "amd.a"}, nil, nil)
	m := New("", 100, 40)
	m.selected = 2
	m = m.setup()
	next, _ := m.Update(modelsMsg{source: m.client, entries: plain(lemonade.Model{ID: "amd.a", Recipe: "cloud"})})
	for _, e := range next.(Model).entries {
		if lemonade.IsHybridID(e.Model.ID) {
			t.Fatal("hybrid listed among the gateway's models")
		}
	}
}
