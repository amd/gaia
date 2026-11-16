// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybrid

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// The record holds only Agent UI turns until the server says otherwise, so
// the line must not read as this terminal's own.
func TestStatsLineSaysWhoseTurnsItCounts(t *testing.T) {
	base := Stats{Turns: 1, CheapTurns: 1, Cost: ptr(0.002832), CostIfAllStrong: ptr(0.04452), SavingPct: ptr(93.6)}
	for scope, want := range map[string]string{
		"":               "Agent UI chats (this terminal's turns aren't counted yet), ",
		"agent_ui":       "Agent UI chats (this terminal's turns aren't counted yet), ",
		"agent_ui_chats": "Agent UI chats (this terminal's turns aren't counted yet), ",
		"all":            "All chats, ",
		"eval":           "Chats in eval, ",
	} {
		s := base
		s.Scope = scope
		if got := s.Line(); !strings.HasPrefix(got, want) {
			t.Errorf("scope %q: %q", scope, got)
		}
	}
	if got := base.Line(); !strings.Contains(got, "$0.0028 spent, $0.04 if all strong · 94% saved") {
		t.Errorf("a sub-cent spend must not read $0.00: %q", got)
	}
}

func ptr(v float64) *float64 { return &v }

// The server's scope_label is its own plain words; it wins over the TUI's guess.
func TestStatsLineShowsTheServersScopeLabelVerbatim(t *testing.T) {
	label := "Agent UI chats only; turns from the terminal UI and `gaia -p` aren't counted yet"
	s := Stats{Turns: 2, CheapTurns: 1, StrongTurns: 1, Cost: ptr(0.5), CostIfAllStrong: ptr(1), Scope: "agent_ui_chats", ScopeLabel: label}
	if got := s.Line(); !strings.HasPrefix(got, label+", ") {
		t.Fatalf("%q", got)
	}
	if got := (Stats{Scope: "all", ScopeLabel: "Every chat"}).Line(); got != "Every chat: no hybrid turns recorded yet." {
		t.Fatalf("%q", got)
	}
}

// An unknown price is null from the server; it must read as unknown, never $0.00.
func TestStatsLineShowsAnUnknownCostAsUnknown(t *testing.T) {
	s := Stats{Turns: 3, CheapTurns: 2, StrongTurns: 1, UnpricedTurns: 1}
	got := s.Line()
	if strings.Contains(got, "$0.00") || !strings.Contains(got, "cost unknown, unknown if all strong") ||
		!strings.Contains(got, "1 turn at an unknown price") || strings.Contains(got, "saved") {
		t.Fatalf("%q", got)
	}
}

func TestStatsDecodesNullCosts(t *testing.T) {
	var s Stats
	raw := `{"turns":1,"cheap_turns":1,"cost":null,"cost_if_all_strong":null,"saving_pct":null,"unpriced_turns":1,"scope":"agent_ui_chats","scope_label":"Agent UI chats only"}`
	if err := json.Unmarshal([]byte(raw), &s); err != nil {
		t.Fatal(err)
	}
	if s.Cost != nil || s.CostIfAllStrong != nil || s.SavingPct != nil || s.UnpricedTurns != 1 || s.ScopeLabel != "Agent UI chats only" {
		t.Fatalf("%+v", s)
	}
}

func TestStatsDateIsADay(t *testing.T) {
	ts := "2026-11-16T17:16:50.589611+00:00"
	want := mustParse(t, ts).Local().Format("2 Jan 2006")
	if got := (Stats{Turns: 1, Since: ts}).Line(); !strings.Contains(got, "since "+want+":") || strings.Contains(got, "T17:") {
		t.Fatalf("%q", got)
	}
}

func mustParse(t *testing.T, ts string) time.Time {
	t.Helper()
	v, err := time.Parse(time.RFC3339Nano, ts)
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func TestServerDownSaysWhatToDoWithoutTheDialError(t *testing.T) {
	err := (&ServerDownError{Base: "http://localhost:4200", What: "Trying a request",
		Err: errors.New("dial tcp 127.0.0.1:4200: connectex: refused")}).Error()
	if strings.Contains(err, "dial tcp") || !strings.Contains(err, "gaia chat --ui") || !strings.Contains(err, AgentUIURLEnv) {
		t.Fatal(err)
	}
}

func TestAgentUIAddressCanBeMoved(t *testing.T) {
	for raw, want := range map[string]string{
		"":                        AgentUIURL,
		"http://localhost:8080":   "http://localhost:8080",
		"http://127.0.0.1:9000/":  "http://127.0.0.1:9000",
		"https://localhost:8080":  "",
		"http://example.com:4200": "",
		"http://localhost":        "",
		"http://localhost:1/api":  "",
	} {
		t.Setenv(AgentUIURLEnv, raw)
		got, err := agentUIBase()
		if want == "" {
			if err == nil || !strings.Contains(err.Error(), AgentUIURLEnv) {
				t.Errorf("%q accepted as %q", raw, got)
			}
			continue
		}
		if err != nil || got != want {
			t.Errorf("%q: %q %v, want %q", raw, got, err, want)
		}
	}
	t.Setenv(AgentUIURLEnv, "http://example.com:4200")
	if _, err := newAPI().Stats(t.Context()); err == nil || !strings.Contains(err.Error(), "loopback") {
		t.Fatalf("a bad address must stop the call: %v", err)
	}
}

// A long notice above the templates is shown whole, not cut mid-path.
func TestALongNoticeAboveTheFocusIsNotClipped(t *testing.T) {
	long := errors.New(`C:\Users\someone\.cache\run\home\.gaia\config.json: 'hybrid_policy' is custom but ` +
		`'hybrid_rules' is missing. Edit the rules in /hybrid, or set hybrid_policy back to quality.`)
	m, _ := open(t, lemonade.HybridSettings{}, long, nil)
	next, _ := m.Update(tea.WindowSizeMsg{Width: 70, Height: 30})
	v := ansi.Strip(next.(Model).View())
	if !strings.Contains(v, `C:\Users\someone`) || strings.Contains(v, "quality..") {
		t.Fatalf("notice clipped or doubled period:\n%s", v)
	}
}
