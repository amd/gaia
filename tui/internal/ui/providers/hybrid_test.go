// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/hybridapi/hybridapitest"
	"github.com/amd/gaia/tui/internal/lemonade"
)

// joined is the screen with wrapped lines rejoined.
func joined(m Model) string {
	lines := strings.Split(ansi.Strip(m.View()), "\n")
	for i := range lines {
		lines[i] = strings.TrimSpace(lines[i])
	}
	return strings.Join(lines, " ")
}

// hybridPanel is the panel on the hybrid row, with the backend's entry read.
func hybridPanel(t *testing.T, entry hybridapi.Entry, err error) Model {
	t.Helper()
	orig := newHybridAPI
	t.Cleanup(func() { newHybridAPI = orig })
	newHybridAPI = func() hybridapi.API { return &hybridapitest.Fake{EntryAnswer: entry, EntryErr: err} }
	m := New("", 100, 40).WithCurrent(lemonade.HybridProvider)
	if m.chosen() != lemonade.HybridProvider {
		t.Fatalf("panel did not open on hybrid: %s", m.chosen())
	}
	next, _ := m.Update(m.fetchEntry()())
	return next.(Model)
}

func TestHybridIsItsOwnRowNamedByTheBackend(t *testing.T) {
	remote, local := true, false
	for _, tc := range []struct {
		entry hybridapi.Entry
		want  string
	}{
		{hybridapi.Entry{DisplayName: "Hybrid (DeepSeek + Opus)", Remote: &remote, Available: true}, "Hybrid (DeepSeek + Opus) (cloud)"},
		{hybridapi.Entry{DisplayName: "Hybrid (Gemma + Qwen3.6)", Remote: &local, Available: true}, "Hybrid (Gemma + Qwen3.6) (local)"},
	} {
		v := joined(hybridPanel(t, tc.entry, nil))
		if !strings.Contains(v, "› "+tc.want) || !strings.Contains(v, "/hybrid changes the models") {
			t.Errorf("want %q:\n%s", tc.want, v)
		}
	}
}

func TestChoosingHybridSelectsTheHybridID(t *testing.T) {
	m := hybridPanel(t, hybridapi.Entry{DisplayName: "Hybrid (Gemma + Opus)", Available: true}, nil)
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd == nil {
		t.Fatal("enter on hybrid did nothing")
	}
	if got := cmd().(SelectedMsg).ID; got != lemonade.HybridModelID {
		t.Fatalf("selected %q", got)
	}
}

func TestHybridRowSaysWhyItCannotBeChosen(t *testing.T) {
	why := "Local only is on, and this sends turns to the cloud (amd.x)"
	for name, tc := range map[string]struct {
		entry hybridapi.Entry
		err   error
		want  string
	}{
		"backend reason": {hybridapi.Entry{DisplayName: "Hybrid (A + B)", Reason: &why}, nil, "Local only is on"},
		"no backend": {hybridapi.Entry{}, &hybridapi.UnreachableError{What: "Naming hybrid", Reason: "isn't running"},
			"needs GAIA's background service"},
	} {
		t.Run(name, func(t *testing.T) {
			m := hybridPanel(t, tc.entry, tc.err)
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

// A slow backend must not hold up the providers list, and hybrid can't be
// chosen until it has answered.
func TestProvidersAreListedWithoutWaitingForHybridsEntry(t *testing.T) {
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	orig := newHybridAPI
	t.Cleanup(func() { newHybridAPI = orig })
	newHybridAPI = func() hybridapi.API { return blockingAPI{Fake: &hybridapitest.Fake{}, release: release} }
	m := New("", 100, 40).WithCurrent(lemonade.HybridProvider)
	done := make(chan tea.Msg, 1)
	go func() { done <- m.readProviders()() }()
	select {
	case msg := <-done:
		if _, ok := msg.(loadedMsg); !ok {
			t.Fatalf("readProviders answered %T", msg)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("listing the providers waited on hybrid's entry")
	}
	if v := joined(m); !strings.Contains(v, "Checking hybrid's settings") {
		t.Fatalf("row before the entry:\n%s", v)
	}
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if cmd != nil || !strings.Contains(next.(Model).note, "Still checking") {
		t.Fatalf("hybrid chosen before its entry arrived: %q", next.(Model).note)
	}
}

type blockingAPI struct {
	*hybridapitest.Fake
	release chan struct{}
}

func (b blockingAPI) Entry(ctx context.Context) (hybridapi.Entry, error) {
	select {
	case <-b.release:
	case <-ctx.Done():
	}
	return hybridapi.Entry{}, errors.New("released")
}

// Hybrid is not one of the gateway's models any more: its own row replaces it.
func TestTheGatewayListHasNoHybridRow(t *testing.T) {
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
