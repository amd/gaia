// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybrid

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/lemonade"
)

type stubs struct {
	saved   []lemonade.HybridSettings
	saveErr error
}

// open builds the screen over stubbed config, gateway list and Agent UI.
func open(t *testing.T, s lemonade.HybridSettings, readErr error, server http.Handler) (Model, *stubs) {
	t.Helper()
	st := &stubs{}
	origRead, origSave, origList, origURL := readSettings, saveSettings, listGatewayModels, AgentUIURL
	t.Cleanup(func() {
		readSettings, saveSettings, listGatewayModels, AgentUIURL = origRead, origSave, origList, origURL
	})
	readSettings = func() (lemonade.HybridSettings, error) { return s, readErr }
	saveSettings = func(s lemonade.HybridSettings) error {
		if err := s.Validate(); err != nil {
			return err
		}
		if st.saveErr != nil {
			return st.saveErr
		}
		st.saved = append(st.saved, s)
		return nil
	}
	listGatewayModels = func(context.Context, string) ([]string, error) {
		return []string{"amd.DeepSeek-V4.1-Flash", "amd.Claude-Opus-5-5", "amd.Gemma-4-31B"}, nil
	}
	if server != nil {
		srv := httptest.NewServer(server)
		t.Cleanup(srv.Close)
		AgentUIURL = srv.URL
	} else {
		AgentUIURL = closedURL(t)
	}
	m := New("", 110, 60)
	m = settle(t, m, m.Init())
	return m, st
}

// closedURL is a loopback address nothing listens on.
func closedURL(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	l.Close()
	return "http://" + addr
}

// settle runs a command and the screen's own messages it leads to, as Bubble
// Tea would. Cursor blinks never settle, so anything else is dropped unrun.
func settle(t *testing.T, m Model, cmd tea.Cmd) Model {
	t.Helper()
	queue := []tea.Cmd{cmd}
	for len(queue) > 0 {
		c := queue[0]
		queue = queue[1:]
		if c == nil {
			continue
		}
		done := make(chan tea.Msg, 1)
		go func() { done <- c() }()
		var msg tea.Msg
		select {
		case msg = <-done:
		case <-time.After(100 * time.Millisecond):
			// A blink tick: real time, nothing the screen waits on.
			continue
		}
		switch v := msg.(type) {
		case tea.BatchMsg:
			queue = append(queue, v...)
		case settingsMsg, modelsMsg, statsMsg, previewMsg:
			next, more := m.Update(msg)
			m = next.(Model)
			queue = append(queue, more)
		}
	}
	return m
}

func key(t *testing.T, m Model, keys ...string) Model {
	t.Helper()
	for _, k := range keys {
		var msg tea.KeyMsg
		switch k {
		case "up":
			msg = tea.KeyMsg{Type: tea.KeyUp}
		case "down":
			msg = tea.KeyMsg{Type: tea.KeyDown}
		case "left":
			msg = tea.KeyMsg{Type: tea.KeyLeft}
		case "right":
			msg = tea.KeyMsg{Type: tea.KeyRight}
		case "enter":
			msg = tea.KeyMsg{Type: tea.KeyEnter}
		case "esc":
			msg = tea.KeyMsg{Type: tea.KeyEsc}
		case "space":
			msg = tea.KeyMsg{Type: tea.KeySpace, Runes: []rune(" ")}
		case "backspace":
			msg = tea.KeyMsg{Type: tea.KeyBackspace}
		case "ctrl+s":
			msg = tea.KeyMsg{Type: tea.KeyCtrlS}
		default:
			msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
		}
		next, cmd := m.Update(msg)
		m = settle(t, next.(Model), cmd)
	}
	return m
}

func focus(t *testing.T, m Model, row int) Model {
	t.Helper()
	for m.focus != row {
		m = key(t, m, "down")
	}
	return m
}

// view is the screen as text, with wrapped lines joined so a check never
// depends on where a sentence breaks.
func view(m Model) string {
	lines := strings.Split(ansi.Strip(m.View()), "\n")
	for i := range lines {
		lines[i] = strings.TrimSpace(lines[i])
	}
	return strings.Join(lines, " ")
}

func TestScreenShowsTheSavedSettings(t *testing.T) {
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	v := view(m)
	for _, want := range []string{
		"amd.DeepSeek-V4.1-Flash (default)", "amd.Claude-Opus-5-5 (default)", "same as the cheap model",
		"15 seconds", "[Quality]", "writing", "difficulty 3", "finding bugs", "Never",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("view lacks %q:\n%s", want, v)
		}
	}
}

func TestABrokenConfigIsShownAndCanBeReplaced(t *testing.T) {
	m, st := open(t, lemonade.HybridSettings{}, errors.New("config.json: 'hybrid_policy' must be one of quality, balanced, custom"), nil)
	if !strings.Contains(view(m), "can't be used") || !strings.Contains(view(m), "hybrid_policy") {
		t.Fatalf("the read error is not shown:\n%s", view(m))
	}
	m = key(t, m, "ctrl+s")
	if len(st.saved) != 1 || st.saved[0].Policy != lemonade.HybridPolicyQuality {
		t.Fatalf("saving should replace the bad value with the defaults: %+v", st.saved)
	}
}

func TestPickingModelsAndSaving(t *testing.T) {
	m, st := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	m = focus(t, m, rowStrong)
	m = key(t, m, "enter", "g", "e", "m", "enter")
	if m.edit.Strong != "amd.Gemma-4-31B" {
		t.Fatalf("strong = %q", m.edit.Strong)
	}
	m = key(t, m, "ctrl+s")
	if len(st.saved) != 1 || st.saved[0].Strong != "amd.Gemma-4-31B" || st.saved[0].Rules != nil {
		t.Fatalf("saved %+v", st.saved)
	}
	if !strings.Contains(view(m), "Saved") {
		t.Fatalf("no confirmation:\n%s", view(m))
	}
}

func TestPickerOffersTheDefaultAndKeepsAnUnlistedChoice(t *testing.T) {
	s := lemonade.DefaultHybridSettings()
	s.Cheap = "fireworks.glm-5p3-flash"
	m, _ := open(t, s, nil, nil)
	m = key(t, m, "enter")
	opts := m.pickOptions()
	if opts[0] != "" || opts[len(opts)-1] != "fireworks.glm-5p3-flash" {
		t.Fatalf("options %v", opts)
	}
	m = key(t, m, "enter") // the default
	if m.edit.Cheap != "" {
		t.Fatalf("default not chosen: %q", m.edit.Cheap)
	}
}

func TestValidationErrorsAreShownAndNothingIsSaved(t *testing.T) {
	m, st := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	// Strong = the cheap default.
	m = focus(t, m, rowStrong)
	m = key(t, m, "enter", "d", "e", "e", "p", "enter", "ctrl+s")
	if len(st.saved) != 0 || !strings.Contains(view(m), "nothing to route between") {
		t.Fatalf("saved %+v\n%s", st.saved, view(m))
	}
	m.edit.Strong = ""
	m = focus(t, m, rowTimeout)
	m = key(t, m, "backspace", "backspace", "x", "ctrl+s")
	if len(st.saved) != 0 || !strings.Contains(view(m), "enter the classifier limit") {
		t.Fatalf("empty timeout: saved %+v\n%s", st.saved, view(m))
	}
	m = key(t, m, "2", "0", "0", "ctrl+s")
	if len(st.saved) != 0 || !strings.Contains(view(m), "1-120 seconds") {
		t.Fatalf("200 s: saved %+v\n%s", st.saved, view(m))
	}
	m = key(t, m, "backspace", "ctrl+s")
	if len(st.saved) != 1 || st.saved[0].Timeout != 20 {
		t.Fatalf("20 s: %+v", st.saved)
	}
}

func TestCustomRulesEditing(t *testing.T) {
	m, st := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	m = focus(t, m, rowPolicy)
	m = key(t, m, "right")
	if m.edit.Policy != lemonade.HybridPolicyBalanced {
		t.Fatalf("policy = %s", m.edit.Policy)
	}
	// Editing a preset's row switches to Custom, starting from that preset.
	m = focus(t, m, rowRules+4) // change_code
	m = key(t, m, "right")
	if m.edit.Policy != lemonade.HybridPolicyCustom {
		t.Fatal("editing a preset row did not switch to Custom")
	}
	if got := m.edit.Rules["change_code"]; got.CheapMaxDifficulty != 4 || !got.AllowHighStakes {
		t.Fatalf("not started from Balanced: %+v", got)
	}
	if !strings.Contains(view(m), "starting from Balanced") {
		t.Fatalf("the switch is not said:\n%s", view(m))
	}
	m = key(t, m, "space")
	m = focus(t, m, rowRules+7) // other
	m = key(t, m, "left", "right", "right", "right", "right", "right", "right", "right")
	if got := m.edit.Rules["other"].CheapMaxDifficulty; got != lemonade.MaxHybridDifficulty {
		t.Fatalf("difficulty is not clamped to 0-5: %d", got)
	}
	m = key(t, m, "ctrl+s")
	if len(st.saved) != 1 {
		t.Fatalf("not saved: %s", view(m))
	}
	got := st.saved[0]
	if got.Policy != lemonade.HybridPolicyCustom || got.Rules["change_code"].AllowHighStakes ||
		got.Rules["other"].CheapMaxDifficulty != 5 || len(got.Rules) != len(lemonade.HybridKinds) {
		t.Fatalf("saved %+v", got)
	}

	// Start from Quality replaces the whole table.
	m = key(t, m, "q")
	if !sameRules(m.edit.Rules, lemonade.HybridPreset(lemonade.HybridPolicyQuality)) {
		t.Fatalf("q did not start from Quality: %+v", m.edit.Rules)
	}
	m = key(t, m, "b")
	if !sameRules(m.edit.Rules, lemonade.HybridPreset(lemonade.HybridPolicyBalanced)) {
		t.Fatalf("b did not start from Balanced: %+v", m.edit.Rules)
	}
	if !m.dirty() {
		t.Fatal("an edited table must read as unsaved")
	}
}

func sameRules(a, b lemonade.HybridRules) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

func TestEscWithUnsavedChangesAsksFirst(t *testing.T) {
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	m = focus(t, m, rowPolicy)
	m = key(t, m, "right")
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = next.(Model)
	if cmd != nil {
		t.Fatal("the first esc over unsaved changes closed the screen")
	}
	_, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		t.Fatal("the second esc did not close")
	}
	if _, ok := cmd().(ClosedMsg); !ok {
		t.Fatal("esc did not close the screen")
	}
}

func TestPreviewPostsTheTextAndShowsTheRoute(t *testing.T) {
	var gotBody map[string]string
	var gotHeader string
	mux := http.NewServeMux()
	mux.HandleFunc("/api/hybrid/stats", func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"since":"2026-11-01","turns":10,"cheap_turns":7,"strong_turns":3,"failed_closed":1,"cost":0.5,"cost_if_all_strong":2.0,"saving_pct":75}`)
	})
	mux.HandleFunc("/api/hybrid/preview", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method", http.StatusMethodNotAllowed)
			return
		}
		gotHeader = r.Header.Get("X-Gaia-UI")
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = io.WriteString(w, `{"model":"amd.DeepSeek-V4.1-Flash","kind":"write_prose","difficulty":1,"stakes":"low","reason":"a short email","classified":true,"seconds":0.8,"policy":"quality"}`)
	})
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, mux)
	if v := view(m); !strings.Contains(v, "Since 2026-11-01: 10 turns: 7 on the cheap model, 3 on the strong (1 unclassified)") ||
		!strings.Contains(v, "75% saved") {
		t.Fatalf("stats line missing:\n%s", v)
	}
	m = focus(t, m, rowTry())
	m = key(t, m, "write a thank-you note", "enter")
	if gotBody["text"] != "write a thank-you note" || gotHeader != "1" {
		t.Fatalf("sent %v with X-Gaia-UI %q", gotBody, gotHeader)
	}
	v := view(m)
	if !strings.Contains(v, "→ DeepSeek (writing, d1) · 0.8s") || !strings.Contains(v, "a short email") {
		t.Fatalf("route not shown:\n%s", v)
	}
}

func TestPreviewWithNoServerSaysItNeedsOne(t *testing.T) {
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	if !strings.Contains(view(m), "Showing routing stats needs the GAIA Agent UI server") {
		t.Fatalf("stats without a server:\n%s", view(m))
	}
	m = focus(t, m, rowTry())
	m = key(t, m, "hello", "enter")
	v := view(m)
	if !strings.Contains(v, "Trying a request needs the GAIA Agent UI server") || !strings.Contains(v, "gaia chat --ui") {
		t.Fatalf("no server must be said plainly:\n%s", v)
	}
}

func TestPreviewServerErrors(t *testing.T) {
	for name, tc := range map[string]struct {
		status int
		body   string
		want   string
	}{
		"older server": {http.StatusNotFound, `{"detail":"Not Found"}`, "older than this TUI"},
		"refused":      {http.StatusUnprocessableEntity, `{"detail":"text must not be empty"}`, "text must not be empty"},
		"list detail":  {http.StatusUnprocessableEntity, `{"detail":[{"msg":"field required"}]}`, "field required"},
		"no model":     {http.StatusOK, `{"kind":"other"}`, "named no model"},
		"not json":     {http.StatusOK, `oops`, "isn't JSON"},
	} {
		t.Run(name, func(t *testing.T) {
			h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			})
			m, _ := open(t, lemonade.DefaultHybridSettings(), nil, h)
			m = focus(t, m, rowTry())
			m = key(t, m, "x", "enter")
			if !strings.Contains(view(m), tc.want) {
				t.Fatalf("want %q:\n%s", tc.want, view(m))
			}
		})
	}
}

func TestUnclassifiedPreviewSaysSo(t *testing.T) {
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"model":"amd.Claude-Opus-5-5","classified":false,"reason":"classifier timed out after 15s","policy":"quality"}`)
	})
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, h)
	m = focus(t, m, rowTry())
	m = key(t, m, "x", "enter")
	if v := view(m); !strings.Contains(v, "→ Opus (couldn't classify the request)") || !strings.Contains(v, "timed out") {
		t.Fatalf("fail-closed preview:\n%s", v)
	}
}

func TestStatsLineWithNoTurns(t *testing.T) {
	if got := (Stats{}).Line(); got != "No hybrid turns recorded yet." {
		t.Fatal(got)
	}
}

func TestViewFitsSmallWindows(t *testing.T) {
	m, _ := open(t, lemonade.DefaultHybridSettings(), nil, nil)
	for _, size := range [][2]int{{60, 20}, {40, 15}, {120, 50}} {
		next, _ := m.Update(tea.WindowSizeMsg{Width: size[0], Height: size[1]})
		lines := strings.Split(ansi.Strip(next.(Model).View()), "\n")
		if len(lines) > size[1] {
			t.Errorf("%dx%d: %d lines", size[0], size[1], len(lines))
		}
		for _, l := range lines {
			if ansi.StringWidth(l) > size[0] {
				t.Errorf("%dx%d: line too wide: %q", size[0], size[1], l)
			}
		}
	}
}
