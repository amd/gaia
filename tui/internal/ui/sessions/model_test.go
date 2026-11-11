// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package sessions

import (
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/sessionstore"
)

// go test ./internal/ui/sessions -update rewrites testdata/frames/*.txt.
var update = flag.Bool("update", false, "rewrite golden frames under testdata/frames")

var epoch = time.Date(2026, 10, 15, 14, 0, 0, 0, time.UTC)

type idx struct {
	id, title, kind, status string
	ago                     time.Duration
	pid, turns              int
}

func seed(t *testing.T, rows ...idx) string {
	t.Helper()
	dir := t.TempDir()
	for _, r := range rows {
		kind := r.kind
		if kind == "" {
			kind = sessionstore.KindChat
		}
		updated := float64(epoch.Add(-r.ago).UnixNano()) / 1e9
		raw, _ := json.Marshal(map[string]any{
			"version": 1, "id": r.id, "title": r.title, "kind": kind, "status": r.status,
			"created_at": updated, "updated_at": updated, "pid": r.pid, "turns": r.turns,
		})
		if err := os.WriteFile(filepath.Join(dir, r.id+".json"), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// liveTUI and liveOther are the pids the open sessions were written by.
const (
	liveTUI   = 100
	liveOther = 200
)

// openIDs are the sessions an agent holds in these frames.
var openIDs = map[string]bool{"aaaa0002": true, "aaaa0003": true, "aaaa0007": true}

func held(lock string) (bool, error) {
	return openIDs[strings.TrimSuffix(filepath.Base(lock), ".lock")], nil
}

func standard(t *testing.T) string {
	return seed(t,
		idx{id: "aaaa0001", title: "Summarise the Q3 board pack into five bullets", status: "done", ago: 3 * time.Hour, turns: 4},
		idx{id: "aaaa0002", title: "Clean up ~/Downloads and archive old installers", status: "waiting", ago: 12 * time.Minute, pid: liveOther, turns: 2},
		idx{id: "aaaa0003", title: "Draft the release notes for v0.25.0", status: "running", ago: 30 * time.Second, pid: liveTUI, turns: 1},
		idx{id: "aaaa0004", title: "Find every contract that renews in November", status: "waiting", ago: 2 * 24 * time.Hour, pid: 999, turns: 3},
		idx{id: "aaaa0005", title: "Triage my inbox", status: "failed", ago: 26 * time.Hour, turns: 1},
		idx{id: "aaaa0006", title: "Nightly eval: rag_quality", kind: "eval", status: "done", ago: time.Hour, turns: 9},
		idx{id: "aaaa0007", title: "Morning briefing", kind: "scheduled", status: "waiting", ago: 5 * time.Minute, pid: liveOther, turns: 1},
	)
}

func panel(t *testing.T, dir, current string, all bool, w, h int) Model {
	t.Helper()
	m := New(dir, current, all, held, w, h).WithClock(func() time.Time { return epoch })
	return send(t, m, m.Init()())
}

func send(t *testing.T, m Model, msg tea.Msg) Model {
	t.Helper()
	next, _ := m.Update(msg)
	return next.(Model)
}

func key(s string) tea.KeyMsg {
	switch s {
	case "down":
		return tea.KeyMsg{Type: tea.KeyDown}
	case "enter":
		return tea.KeyMsg{Type: tea.KeyEnter}
	case "esc":
		return tea.KeyMsg{Type: tea.KeyEsc}
	}
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func plain(view string) string {
	rows := strings.Split(ansi.Strip(view), "\n")
	for i, r := range rows {
		rows[i] = strings.TrimRight(r, " ")
	}
	return strings.Join(rows, "\n") + "\n"
}

func golden(t *testing.T, name, got string) {
	t.Helper()
	path := filepath.Join("testdata", "frames", name+".txt")
	if *update {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(got), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("no golden frame %s (run with -update): %v\n%s", path, err, got)
	}
	if w := strings.ReplaceAll(string(want), "\r\n", "\n"); w != got {
		t.Errorf("frame %s changed.\n--- want\n%s\n--- got\n%s", name, w, got)
	}
}

func withProfile(t *testing.T, p termenv.Profile) {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(p)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
}

func TestFrameSessionList(t *testing.T) {
	m := panel(t, standard(t), "aaaa0003", false, 100, 24)
	golden(t, "list-100", plain(m.View()))
}

func TestFrameSessionListAt40Columns(t *testing.T) {
	m := panel(t, standard(t), "aaaa0003", false, 40, 30)
	got := plain(m.View())
	for i, row := range strings.Split(got, "\n") {
		if w := ansi.StringWidth(row); w > 40 {
			t.Errorf("row %d is %d columns wide: %q", i, w, row)
		}
	}
	golden(t, "list-40", got)
}

func TestFrameSessionListAllKinds(t *testing.T) {
	m := panel(t, standard(t), "aaaa0003", true, 100, 24)
	golden(t, "list-all-kinds", plain(m.View()))
}

func TestFrameEmpty(t *testing.T) {
	golden(t, "empty", plain(panel(t, seed(t), "", false, 80, 20).View()))
}

func TestADamagedIndexIsShownNotSkipped(t *testing.T) {
	dir := seed(t, idx{id: "aaaa0001", title: "Kept", status: "done", turns: 1})
	if err := os.WriteFile(filepath.Join(dir, "bbbb0001.json"), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	got := strings.Join(strings.Fields(plain(panel(t, dir, "", false, 400, 20).View())), " ")
	for _, want := range []string{"Kept", "bbbb0001.json is not valid JSON", "delete it"} {
		if !strings.Contains(got, want) {
			t.Errorf("panel lacks %q:\n%s", want, got)
		}
	}
}

// Under NO_COLOR every status still reads in words, and nothing but text is drawn.
func TestFrameSessionListNoColor(t *testing.T) {
	withProfile(t, termenv.Ascii)
	m := panel(t, standard(t), "aaaa0003", false, 100, 24)
	raw := m.View()
	if strings.Contains(raw, "\x1b") {
		t.Fatalf("NO_COLOR frame carries escape codes: %q", raw)
	}
	golden(t, "list-no-color", plain(raw))
	for _, want := range []string{"waiting on you", "running", "done", "failed", "stopped mid-turn"} {
		if !strings.Contains(raw, want) {
			t.Errorf("NO_COLOR frame lacks %q", want)
		}
	}
}

func TestColourOnlyRepeatsTheWords(t *testing.T) {
	withProfile(t, termenv.TrueColor)
	coloured := panel(t, standard(t), "aaaa0003", false, 100, 24).View()
	if !strings.Contains(coloured, "\x1b[") {
		t.Fatal("expected colour in a TrueColor frame")
	}
	lipgloss.SetColorProfile(termenv.Ascii)
	bare := panel(t, standard(t), "aaaa0003", false, 100, 24).View()
	if plain(coloured) != plain(bare) {
		t.Fatalf("colour changed the text:\n%s\n---\n%s", plain(coloured), plain(bare))
	}
}

func TestWaitingOnYouIsListedFirst(t *testing.T) {
	m := panel(t, standard(t), "", false, 100, 24)
	first := m.listing.Sessions[0]
	if first.Status != sessionstore.StatusWaiting || first.ID != "aaaa0002" {
		t.Fatalf("first row = %+v", first)
	}
}

func TestEnterReopensTheSelectedSession(t *testing.T) {
	m := panel(t, standard(t), "", false, 100, 24)
	m = send(t, m, key("down"))
	_, cmd := m.Update(key("enter"))
	got, ok := cmd().(SelectedMsg)
	if !ok || got.Session.ID != m.listing.Sessions[1].ID {
		t.Fatalf("enter sent %#v", cmd())
	}
}

func TestEscClosesWithoutPicking(t *testing.T) {
	m := panel(t, standard(t), "", false, 100, 24)
	_, cmd := m.Update(key("esc"))
	if _, ok := cmd().(ClosedMsg); !ok {
		t.Fatalf("esc sent %#v", cmd())
	}
}

func TestAShowsEveryKindAndBack(t *testing.T) {
	m := panel(t, standard(t), "", false, 100, 24)
	hidden := len(m.listing.Sessions)
	next, cmd := m.Update(key("a"))
	m = send(t, next.(Model), cmd())
	if len(m.listing.Sessions) != hidden+2 {
		t.Fatalf("all kinds listed %d, want %d", len(m.listing.Sessions), hidden+2)
	}
	next, cmd = m.Update(key("a"))
	m = send(t, next.(Model), cmd())
	if len(m.listing.Sessions) != hidden {
		t.Fatalf("back to chats listed %d", len(m.listing.Sessions))
	}
}

func TestEnterDoesNothingWhileLoading(t *testing.T) {
	m := New(standard(t), "", false, held, 100, 24)
	if _, cmd := m.Update(key("enter")); cmd != nil {
		t.Fatal("enter while loading picked something")
	}
}

func TestAStaleLoadIsDropped(t *testing.T) {
	m := panel(t, standard(t), "", false, 100, 24)
	stale := loadedMsg{gen: m.gen - 1}
	if got := send(t, m, stale); len(got.listing.Sessions) != len(m.listing.Sessions) {
		t.Fatal("a superseded load replaced the list")
	}
}
