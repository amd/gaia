// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/update"
)

func blockedGate(t *testing.T, opts Options) Model {
	t.Helper()
	t.Setenv("GAIA_DISABLE_UPDATE", "")
	prev := update.Current
	update.Current = "0.25.0"
	t.Cleanup(func() { update.Current = prev; update.Cancel() })
	m := New(newFake(), testConfig(), opts)
	m.rep = Report{Rows: []Row{{Key: KeyModel, Label: "AI model", State: StateFailed, Line: "too old"}}}
	m.phase = phaseIdle
	return m
}

func pressU(m Model) (Model, tea.Cmd) {
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'u'}})
	return next.(Model), cmd
}

func TestABlockedGateOffersAnUpdate(t *testing.T) {
	m := blockedGate(t, Options{OfferUpdate: true})
	if !strings.Contains(m.footer(120), "update GAIA") {
		t.Fatalf("footer = %q", m.footer(120))
	}
	next, cmd := pressU(m)
	if cmd == nil || !next.Busy() {
		t.Fatal("u must start a check")
	}
	if strings.Contains(next.footer(120), "update GAIA") {
		t.Error("the key is still offered while it runs")
	}
}

func TestUpdateIsNotOfferedWhereItCannotRun(t *testing.T) {
	if _, cmd := pressU(blockedGate(t, Options{})); cmd != nil {
		t.Error("a host that did not ask for it got the key")
	}
	m := blockedGate(t, Options{OfferUpdate: true})
	t.Setenv("GAIA_DISABLE_UPDATE", "1")
	if _, cmd := pressU(m); cmd != nil {
		t.Error("GAIA_DISABLE_UPDATE=1 must switch the key off")
	}
	m = blockedGate(t, Options{OfferUpdate: true})
	update.Current = "dev"
	if _, cmd := pressU(m); cmd != nil {
		t.Error("a dev build is never offered an update")
	}
}

func TestAnUpdateFoundOnTheGateQueuesAndCloses(t *testing.T) {
	m := blockedGate(t, Options{OfferUpdate: true})
	plan := update.Plan{Tag: "v0.26.0", Kind: update.KindScript}
	_, cmd := m.Update(updateFoundMsg{plan: plan, gate: m.cancel})
	if p, ok := update.Pending(); !ok || p.Tag != "v0.26.0" {
		t.Fatalf("pending = %+v, %v", p, ok)
	}
	if cmd == nil {
		t.Fatal("the install runs after the TUI exits, so the gate must quit")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatal("the command was not tea.Quit")
	}
}

func TestNoUpdateOnTheGateSaysWhyAndStays(t *testing.T) {
	m := blockedGate(t, Options{OfferUpdate: true})
	m.phase = phaseFixing
	next, cmd := m.Update(updateFoundMsg{note: "It is up to date.", gate: m.cancel})
	if got := next.(Model); cmd != nil || got.Busy() || got.note != "It is up to date." {
		t.Fatalf("note = %q, busy = %v", got.note, got.Busy())
	}
	next, cmd = m.Update(updateFoundMsg{err: errors.New("cannot reach GitHub"), gate: m.cancel})
	if got := next.(Model); cmd != nil || !strings.Contains(got.note, "cannot reach GitHub") {
		t.Fatalf("note = %q", got.note)
	}
	if _, ok := update.Pending(); ok {
		t.Error("nothing should be queued")
	}
}

func TestALateUpdateAnswerForAnotherGateIsDropped(t *testing.T) {
	m := blockedGate(t, Options{OfferUpdate: true})
	_, cmd := m.Update(updateFoundMsg{plan: update.Plan{Tag: "v0.26.0"}, gate: &cancelBox{}})
	if _, ok := update.Pending(); ok || cmd != nil {
		t.Fatal("an answer from a gate the user left acted on this one")
	}
}

func TestBOffersTheVersionBeforeTheUpdate(t *testing.T) {
	m := blockedGate(t, Options{OfferUpdate: true})
	if strings.Contains(m.footer(120), "back to") {
		t.Fatal("offered a way back with no earlier version known")
	}
	m.previous = "v0.25.1"
	if strings.Contains(m.footer(120), "back to") {
		t.Fatal("offered a version newer than the one running")
	}
	m.previous = "v0.24.0"
	if !strings.Contains(m.footer(120), "back to v0.24.0") {
		t.Fatalf("footer = %q", m.footer(120))
	}
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'b'}})
	if cmd == nil || !next.(Model).Busy() {
		t.Fatal("b must start the lookup")
	}
}
