// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package agents

import (
	"context"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// stubLister is a HubAgentLister the tests fully control — no daemon, no
// network.
type stubLister struct {
	catalogOut *catalog.HubCatalog
	catalogErr error
	agentsOut  []catalog.AgentRuntime
	agentsErr  error
}

func (s *stubLister) Catalog(ctx context.Context, start, installedOnly, refresh bool) (*catalog.HubCatalog, error) {
	return s.catalogOut, s.catalogErr
}
func (s *stubLister) Agents(ctx context.Context, start bool) ([]catalog.AgentRuntime, error) {
	return s.agentsOut, s.agentsErr
}

func loadSync(m Model) Model {
	msg := m.load()()
	next, _ := m.Update(msg)
	return next.(Model)
}

// rowFields locates the row whose id token is id and returns everything from
// that token onward, split on whitespace — name, version, state, in that
// column order. Locating by id (rather than asserting the whole view as one
// blob) is what lets a test tie a value to the row it actually came from, so
// swapping two rows' running/stopped fails here instead of passing.
func rowFields(t *testing.T, view, id string) []string {
	t.Helper()
	for _, line := range strings.Split(ansi.Strip(view), "\n") {
		fields := strings.Fields(line)
		for i, tok := range fields {
			if tok == id {
				return fields[i:]
			}
		}
	}
	t.Fatalf("no row for id %q in view: %s", id, view)
	return nil
}

func TestPopulatedListShowsIDNameVersionAndState(t *testing.T) {
	client := &stubLister{
		catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{
			{ID: "gaia", Name: "GAIA", InstalledVersion: "0.2.0"},
			{ID: "email", Name: "Email", InstalledVersion: "0.1.3"},
		}},
		agentsOut: []catalog.AgentRuntime{
			{AgentID: "gaia", State: "running"},
			{AgentID: "email", State: "stopped"},
		},
	}
	m := loadSync(New(client, 80, 24))
	view := m.View()

	gaia := rowFields(t, view, "gaia")
	if len(gaia) < 4 || gaia[1] != "GAIA" || gaia[2] != "v0.2.0" || gaia[3] != "running" {
		t.Fatalf("gaia row wrong: %v", gaia)
	}
	email := rowFields(t, view, "email")
	if len(email) < 4 || email[1] != "Email" || email[2] != "v0.1.3" || email[3] != "stopped" {
		t.Fatalf("email row wrong: %v", email)
	}
}

func TestZeroInstalledAgentsShowsVerbatimMessage(t *testing.T) {
	client := &stubLister{catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{}}}
	m := loadSync(New(client, 80, 24))
	want := "Only the flagship is installed. Install another with: gaia hub install <id>"
	if !strings.Contains(m.View(), want) {
		t.Fatalf("missing verbatim message: %s", m.View())
	}
}

func TestUncachedHubIndexUsesLocallyKnownDisplayName(t *testing.T) {
	client := &stubLister{
		// Mirrors the daemon's fallback when the hub index has never been
		// cached: src/gaia/daemon/sidecars/install.py:207 returns
		// {"id": id, "name": id}.
		catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{
			{ID: "gaia", Name: "gaia"},
			{ID: "email", Name: "email"},
		}},
		agentsOut: []catalog.AgentRuntime{{AgentID: "gaia", State: "running"}},
	}
	m := loadSync(New(client, 80, 24))
	view := m.View()

	gaia := rowFields(t, view, "gaia")
	if len(gaia) < 2 || gaia[1] != "GAIA" {
		t.Fatalf("expected known display name GAIA, got: %v", gaia)
	}
	email := rowFields(t, view, "email")
	if len(email) < 2 || email[1] != "Email" {
		t.Fatalf("expected known display name Email, got: %v", email)
	}
}

func TestOnlyFlagshipInstalledShowsVerbatimMessage(t *testing.T) {
	client := &stubLister{
		catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{{ID: "gaia", Name: "GAIA"}}},
	}
	m := loadSync(New(client, 80, 24))
	want := "Only the flagship is installed. Install another with: gaia hub install <id>"
	if !strings.Contains(m.View(), want) {
		t.Fatalf("missing verbatim message: %s", m.View())
	}
}

func TestDaemonUnreachableShowsActionableErrorNotEmptyList(t *testing.T) {
	client := &stubLister{catalogErr: &daemonDownError{}}
	m := loadSync(New(client, 80, 24))
	view := m.View()
	if !strings.Contains(view, "Could not list installed agents") || !strings.Contains(view, "start one") {
		t.Fatalf("error path is not actionable: %s", view)
	}
	if len(m.rows) != 0 {
		t.Fatal("error path should not report an empty success")
	}
}

type daemonDownError struct{}

func (e *daemonDownError) Error() string {
	return "no GAIA daemon is registered. start one with `gaia daemon start`."
}

func TestSelectionAndCancelKeys(t *testing.T) {
	client := &stubLister{
		catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{
			{ID: "gaia", Name: "GAIA"},
			{ID: "email", Name: "Email"},
		}},
		agentsOut: []catalog.AgentRuntime{{AgentID: "gaia", State: "running"}, {AgentID: "email", State: "stopped"}},
	}
	m := loadSync(New(client, 80, 24))

	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyDown})
	m = next.(Model)
	if m.selected != 1 {
		t.Fatalf("down did not move selection: %d", m.selected)
	}

	before := m.ctx
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	sel, ok := cmd().(SelectedMsg)
	if !ok || sel.ID != "email" {
		t.Fatalf("enter did not select the highlighted row: %+v", sel)
	}
	// Selection drops the fetch that was in flight...
	if before.Err() == nil {
		t.Error("selection did not cancel the load that was in flight")
	}
	// ...but leaves the panel itself live, so a host that re-shows it after an
	// abandoned switch still has a working list.
	if m.ctx.Err() != nil {
		t.Error("selection left the panel's own context cancelled, which makes it deaf to every later key")
	}
}

func TestEscCancelsWithoutSelecting(t *testing.T) {
	client := &stubLister{catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{{ID: "gaia", Name: "GAIA"}}}}
	m := loadSync(New(client, 80, 24))
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if _, ok := cmd().(ClosedMsg); !ok {
		t.Fatal("esc did not close the panel")
	}
}

func TestAbandonedLoadResultsCannotLandOnTheCurrentList(t *testing.T) {
	m := New(&stubLister{catalogOut: &catalog.HubCatalog{}}, 80, 24)
	stale := m.gen
	m.abandonLoad()

	next, _ := m.Update(loadedMsg{gen: stale, rows: []row{{id: "stale"}}})
	m = next.(Model)
	if len(m.rows) != 0 {
		t.Fatal("a result from an abandoned load overwrote the current list")
	}

	// ...while the load that IS current still lands.
	next, _ = m.Update(loadedMsg{gen: m.gen, rows: []row{{id: "gaia"}}})
	if len(next.(Model).rows) != 1 {
		t.Fatal("the current load's result was dropped")
	}
}

func TestPanelNeverOutgrowsItsWindow(t *testing.T) {
	client := &stubLister{
		catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{
			{ID: "gaia", Name: "GAIA"}, {ID: "email", Name: "Email"}, {ID: "another", Name: "Another"},
		}},
		agentsOut: []catalog.AgentRuntime{{AgentID: "gaia", State: "running"}},
	}
	for _, size := range [][2]int{{80, 24}, {40, 10}, {20, 6}} {
		m := loadSync(New(client, size[0], size[1]))
		for _, line := range strings.Split(ansi.Strip(m.View()), "\n") {
			if ansi.StringWidth(line) > size[0] {
				t.Fatalf("overflow at %v: %q", size, line)
			}
		}
	}
}

var _ error = (*daemonDownError)(nil)

// A selection the host does not follow through on must leave the panel
// usable. Found on a real machine, not in a test: picking an agent, backing
// out of its readiness gate, and landing back on this panel left Esc — and
// every other key — doing nothing, with no way out of the list.
func TestThePanelStaysUsableAfterAnAbandonedSelection(t *testing.T) {
	client := &stubLister{
		catalogOut: &catalog.HubCatalog{Agents: []catalog.HubEntry{
			{ID: "gaia", Name: "GAIA", InstalledVersion: "0.2.0"},
			{ID: "email", Name: "Email", InstalledVersion: "0.1.3"},
		}},
		agentsOut: []catalog.AgentRuntime{{AgentID: "gaia", State: "running"}},
	}
	m := loadSync(New(client, 80, 24))

	picked, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = picked.(Model)
	if cmd == nil {
		t.Fatal("test setup: Enter should have emitted a SelectedMsg")
	}
	if _, ok := cmd().(SelectedMsg); !ok {
		t.Fatal("test setup: Enter should have emitted a SelectedMsg")
	}

	// The host kept this panel rather than replacing it — Esc must still work.
	closed, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		t.Fatal("Esc after an abandoned selection produced no command — the panel cannot be closed")
	}
	if _, ok := cmd().(ClosedMsg); !ok {
		t.Fatal("Esc after an abandoned selection did not emit ClosedMsg")
	}

	// ...and so must navigation, so the list is not merely dismissible-but-dead.
	moved, _ := closed.(Model).Update(tea.KeyMsg{Type: tea.KeyDown})
	if moved.(Model).selected == 0 {
		t.Error("the panel stopped responding to navigation after an abandoned selection")
	}
}

// A panel that was closed while its list fetch was still in flight must not be
// able to overwrite the NEXT panel's list when that fetch finally returns.
// Reproduced on review: with a per-panel counter both panels start at zero, so
// the dead panel's "context canceled" error landed on the fresh one.
func TestAStalePanelsLoadCannotLandOnTheNextPanel(t *testing.T) {
	first := New(&stubLister{catalogOut: &catalog.HubCatalog{}}, 80, 24)
	inFlight := first.gen // the generation its Init() load is carrying
	first.abandonLoad()   // user pressed Esc while it was still loading

	second := New(&stubLister{catalogOut: &catalog.HubCatalog{}}, 80, 24)
	next, _ := second.Update(loadedMsg{gen: inFlight, err: context.Canceled})
	second = next.(Model)

	if second.loadErr != "" {
		t.Fatalf("a closed panel's abandoned load reached the next panel: %q", second.loadErr)
	}
	if !second.loading {
		t.Error("the fresh panel stopped showing its own load as in-progress")
	}
}
