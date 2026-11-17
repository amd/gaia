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

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/hybridapi/hybridapitest"
)

var kinds = []string{"write_prose", "answer_question", "analyze_data", "operate_tools",
	"change_code", "find_defects", "plan_or_design", "other"}

const presetsJSON = `{
 "quality":{"write_prose":{"cheap_max_difficulty":3,"allow_high_stakes":false},"answer_question":{"cheap_max_difficulty":3,"allow_high_stakes":false},
  "analyze_data":{"cheap_max_difficulty":3,"allow_high_stakes":false},"operate_tools":{"cheap_max_difficulty":3,"allow_high_stakes":false},
  "change_code":{"cheap_max_difficulty":2,"allow_high_stakes":false},"find_defects":{"cheap_max_difficulty":0,"allow_high_stakes":false},
  "plan_or_design":{"cheap_max_difficulty":0,"allow_high_stakes":false},"other":{"cheap_max_difficulty":0,"allow_high_stakes":false}},
 "balanced":{"write_prose":{"cheap_max_difficulty":3,"allow_high_stakes":false},"answer_question":{"cheap_max_difficulty":3,"allow_high_stakes":false},
  "analyze_data":{"cheap_max_difficulty":3,"allow_high_stakes":false},"operate_tools":{"cheap_max_difficulty":3,"allow_high_stakes":false},
  "change_code":{"cheap_max_difficulty":3,"allow_high_stakes":true},"find_defects":{"cheap_max_difficulty":0,"allow_high_stakes":false},
  "plan_or_design":{"cheap_max_difficulty":0,"allow_high_stakes":false},"other":{"cheap_max_difficulty":0,"allow_high_stakes":false}}}`

func str(s string) *string { return &s }

// saved is a backend answer: the measured pair on Quality.
func saved() hybridapi.Settings {
	var presets map[string]json.RawMessage
	_ = json.Unmarshal([]byte(presetsJSON), &presets)
	return hybridapi.Settings{
		CheapModel: "amd.DeepSeek-V4.1-Flash", StrongModel: "amd.Claude-Opus-5-5",
		ClassifierModel: "amd.DeepSeek-V4.1-Flash", ClassifierTimeout: "15", Policy: "quality",
		Rules: presets["quality"], Presets: json.RawMessage(presetsJSON), Kinds: kinds,
		Models: []hybridapi.Model{
			{ID: "Gemma-4-E4B-it-GGUF", Provider: "local", Available: true},
			{ID: "amd.Claude-Opus-5-5", Provider: "amd", Available: true},
			{ID: "amd.DeepSeek-V4.1-Flash", Provider: "amd", Available: true},
			{ID: "amd.Gone", Provider: "amd", Reason: str("The AMD LLM Gateway doesn't list amd.Gone")},
		},
		Measured: true,
	}
}

func templates() []hybridapi.Template {
	return []hybridapi.Template{
		{ID: "gateway-deepseek-opus", Name: "DeepSeek + Opus", Description: "The measured pair.", Available: true, Measured: true},
		{ID: "claude-direct", Name: "Claude direct", Reason: str("Add an Anthropic API key")},
	}
}

// open builds the screen over a scripted backend and lets its reads land.
func open(t *testing.T, f *hybridapitest.Fake) Model {
	t.Helper()
	orig := NewAPI
	t.Cleanup(func() { NewAPI = orig })
	NewAPI = func() hybridapi.API { return f }
	m := New(100, 80)
	return settle(t, m, m.Init())
}

func backend() *hybridapitest.Fake {
	return &hybridapitest.Fake{View: saved(), TemplateList: templates(),
		StatsAnswer: hybridapi.Stats{Scope: hybridapi.ScopeAgentUIChats}}
}

// settle runs cmd and every command its messages lead to.
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
		case settingsMsg, savedMsg, templatesMsg, appliedMsg, statsMsg, previewMsg:
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

// focus moves to a field row.
func focus(t *testing.T, m Model, row int) Model {
	t.Helper()
	for i := 0; m.field() != row; i++ {
		if i > 100 {
			t.Fatalf("never reached row %d", row)
		}
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

func TestScreenShowsWhatTheBackendReturns(t *testing.T) {
	m := open(t, backend())
	v := view(m)
	for _, want := range []string{
		"amd.DeepSeek-V4.1-Flash · AMD LLM Gateway", "amd.Claude-Opus-5-5 · AMD LLM Gateway", "15 seconds",
		"[Quality]", "Balanced", "Custom", "code change", "difficulty 2", "DeepSeek + Opus · measured",
		"Claude direct · not yet measured", "Can't use yet: Add an Anthropic API key",
		"No hybrid turns recorded yet (Agent UI chats only).",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("missing %q:\n%s", want, v)
		}
	}
}

func TestAnUnreachableBackendIsAnActionableErrorAndNothingIsWritten(t *testing.T) {
	f := backend()
	f.SettingsErr = &hybridapi.UnreachableError{What: "Reading the hybrid settings", Base: "http://127.0.0.1:5000",
		Reason: "isn't running"}
	m := open(t, f)
	v := view(m)
	for _, want := range []string{"needs GAIA's background service at http://127.0.0.1:5000", "Quit and reopen GAIA"} {
		if !strings.Contains(v, want) {
			t.Fatalf("missing %q:\n%s", want, v)
		}
	}
	m = key(t, m, "down", "enter", "right", "ctrl+s")
	if len(f.Updates) != 0 || len(f.Applied) != 0 {
		t.Fatalf("a screen with no backend sent %v %v", f.Updates, f.Applied)
	}
}

func TestSavingSendsOnlyWhatChangedAndShowsTheAnswer(t *testing.T) {
	f := backend()
	f.UpdateFn = func(changes map[string]any) (hybridapi.Settings, error) {
		s := saved()
		s.Policy = "balanced"
		s.StrongModel = "Gemma-4-E4B-it-GGUF"
		return s, nil
	}
	m := open(t, f)
	m = focus(t, m, rowStrong)
	m = key(t, m, "enter", "gemma", "enter")
	m = focus(t, m, rowPolicy)
	m = key(t, m, "right", "ctrl+s")
	if len(f.Updates) != 1 {
		t.Fatalf("updates %v", f.Updates)
	}
	got := f.Updates[0]
	if len(got) != 2 || got["strong_model"] != "Gemma-4-E4B-it-GGUF" || got["policy"] != "balanced" {
		t.Fatalf("sent %v", got)
	}
	if v := view(m); !strings.Contains(v, "Saved.") || !strings.Contains(v, "[Balanced]") || m.dirty() {
		t.Fatalf("after save:\n%s", v)
	}
}

func TestARefusedSaveShowsTheBackendsReasonAndKeepsTheEdit(t *testing.T) {
	msg := "classifier_timeout must be a whole number of seconds, 1-120, but it is 500."
	f := backend()
	f.UpdateFn = func(map[string]any) (hybridapi.Settings, error) {
		return hybridapi.Settings{}, &hybridapi.StatusError{Status: 422, Detail: msg}
	}
	m := open(t, f)
	m = focus(t, m, rowTimeout)
	m = key(t, m, "backspace", "backspace", "500", "ctrl+s")
	if len(f.Updates) != 1 || f.Updates[0]["classifier_timeout"] != 500 {
		t.Fatalf("sent %v", f.Updates)
	}
	if v := view(m); !strings.Contains(v, "Not saved: "+msg) || !m.dirty() {
		t.Fatalf("refusal:\n%s", v)
	}
}

func TestNothingChangedSendsNothing(t *testing.T) {
	f := backend()
	m := key(t, open(t, f), "ctrl+s")
	if len(f.Updates) != 0 || !strings.Contains(view(m), "Nothing to save.") {
		t.Fatalf("sent %v:\n%s", f.Updates, view(m))
	}
}

func TestPickerGroupsTheBackendsModelsAndSaysWhyOnesCantBeUsed(t *testing.T) {
	m := open(t, backend())
	m = focus(t, m, rowCheap)
	m = key(t, m, "enter")
	v := view(m)
	local, gateway := strings.Index(v, "On this PC"), strings.Index(v, "AMD LLM Gateway")
	if local < 0 || gateway < local || !strings.Contains(v, "amd.Gone · can't use: The AMD LLM Gateway doesn't list amd.Gone") {
		t.Fatalf("picker:\n%s", v)
	}
	m = key(t, m, "Gone", "enter")
	if m.picking >= 0 || !strings.Contains(m.errMsg, "doesn't list amd.Gone") || m.roleValue(rowCheap) != "amd.DeepSeek-V4.1-Flash" {
		t.Fatalf("an unusable model was picked: %q", m.errMsg)
	}
}

func TestTheDefaultIsSavedAsNull(t *testing.T) {
	f := backend()
	m := open(t, f)
	m = focus(t, m, rowClassifier)
	m = key(t, m, "enter", "enter", "ctrl+s")
	if len(f.Updates) != 1 {
		t.Fatalf("updates %v", f.Updates)
	}
	if v, ok := f.Updates[0]["classifier_model"]; !ok || v != nil {
		t.Fatalf("sent %v", f.Updates[0])
	}
}

func TestEditingARuleTurnsCustomOnAndSendsTheTable(t *testing.T) {
	f := backend()
	m := open(t, f)
	m = focus(t, m, rowRules+4) // change_code
	m = key(t, m, "right", "space", "ctrl+s")
	got := f.Updates[0]
	rules, ok := got["rules"].(hybridapi.Rules)
	if got["policy"] != "custom" || !ok || rules["change_code"].CheapMaxDifficulty != 3 ||
		!rules["change_code"].AllowHighStakes || len(rules) != len(kinds) {
		t.Fatalf("sent %v", got)
	}
}

func TestStartingFromAPresetCopiesTheBackendsTable(t *testing.T) {
	m := open(t, backend())
	m = focus(t, m, rowPolicy)
	m = key(t, m, "b")
	if m.policy != "custom" || !m.rules["change_code"].AllowHighStakes {
		t.Fatalf("policy %s rules %v", m.policy, m.rules)
	}
}

func TestApplyingATemplateGoesThroughTheBackend(t *testing.T) {
	f := backend()
	f.ApplyFn = func(id string) (hybridapi.Settings, error) {
		s := saved()
		s.Policy = "balanced"
		return s, nil
	}
	m := key(t, open(t, f), "enter")
	if len(f.Applied) != 1 || f.Applied[0] != "gateway-deepseek-opus" {
		t.Fatalf("applied %v", f.Applied)
	}
	if v := view(m); !strings.Contains(v, "DeepSeek + Opus applied") || !strings.Contains(v, "[Balanced]") {
		t.Fatalf("after apply:\n%s", v)
	}
}

func TestEnterIsIgnoredWhileATemplateIsApplying(t *testing.T) {
	f := backend()
	m := open(t, f)
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	if cmd == nil || !m.applying {
		t.Fatal("enter did not start applying")
	}
	next, again := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if again != nil || !next.(Model).applying {
		t.Fatal("a second enter started a second apply")
	}
}

func TestAnUnavailableTemplateIsNotApplied(t *testing.T) {
	f := backend()
	m := key(t, open(t, f), "down", "enter")
	if len(f.Applied) != 0 || !strings.Contains(m.errMsg, "Add an Anthropic API key") {
		t.Fatalf("applied %v, err %q", f.Applied, m.errMsg)
	}
}

func TestEscWithUnsavedChangesAsksFirst(t *testing.T) {
	m := open(t, backend())
	m = focus(t, m, rowPolicy)
	m = key(t, m, "right")
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = next.(Model)
	if cmd != nil || !strings.Contains(m.note, "unsaved changes") {
		t.Fatalf("esc closed with unsaved changes: %q", m.note)
	}
	_, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if cmd == nil {
		t.Fatal("second esc did not close")
	}
	if _, ok := cmd().(ClosedMsg); !ok {
		t.Fatal("second esc did not close")
	}
}

func TestPreviewShowsTheRoute(t *testing.T) {
	f := backend()
	d := 1
	f.Route = event.CanonicalRoute{Model: "amd.DeepSeek-V4.1-Flash", Kind: "write_prose", Difficulty: &d,
		Stakes: "low", Classified: true, Seconds: 1.2, Reason: "writing at difficulty 1"}
	m := open(t, f)
	m = focus(t, m, m.rowTryAt())
	m = key(t, m, "hi", "enter")
	if len(f.Previews) != 1 || f.Previews[0] != "hi" {
		t.Fatalf("previews %v", f.Previews)
	}
	if v := view(m); !strings.Contains(v, "→ DeepSeek (writing, d1) · 1.2s") || !strings.Contains(v, "writing at difficulty 1") {
		t.Fatalf("preview:\n%s", v)
	}
}

func TestUnclassifiedPreviewSaysSoAndHowToFixIt(t *testing.T) {
	f := backend()
	f.Route = event.CanonicalRoute{Model: "amd.Claude-Opus-5-5", Classified: false,
		Reason: "classifier timed out after 15s", Fix: "Raise the classifier limit, or pick a faster classifier."}
	m := open(t, f)
	m = focus(t, m, m.rowTryAt())
	m = key(t, m, "x", "enter")
	v := view(m)
	reason, fix := strings.Index(v, "timed out"), strings.Index(v, "Raise the classifier limit")
	if !strings.Contains(v, "→ Opus (couldn't classify the request)") || reason < 0 || fix < reason {
		t.Fatalf("fail-closed preview needs its reason, then its fix:\n%s", v)
	}
}

func TestPreviewErrorsAreShown(t *testing.T) {
	f := backend()
	f.PreviewErr = &hybridapi.TimeoutError{What: "Trying a request", Base: "http://127.0.0.1:5000", Limit: 150 * time.Second}
	m := open(t, f)
	m = focus(t, m, m.rowTryAt())
	m = key(t, m, "x", "enter")
	if v := view(m); !strings.Contains(v, "didn't answer within 2m30s") || !strings.Contains(v, "http://127.0.0.1:5000") {
		t.Fatalf("preview error:\n%s", v)
	}
}

// A hand-broken config is shown with what's wrong, and a save can fix it.
func TestABrokenConfigIsShownAndCanBeFixed(t *testing.T) {
	f := backend()
	f.View.ConfigError = str(`C:\Users\someone\.gaia\config.json: 'hybrid_policy' is custom but 'hybrid_rules' ` +
		`is missing. Edit the rules in /hybrid, or set the policy back to quality.`)
	m := open(t, f)
	next, _ := m.Update(tea.WindowSizeMsg{Width: 70, Height: 30})
	v := ansi.Strip(next.(Model).View())
	if !strings.Contains(v, `C:\Users\someone`) || strings.Contains(v, "quality..") {
		t.Fatalf("notice clipped or doubled period:\n%s", v)
	}
	if strings.Contains(v, "gaia config") || strings.Contains(v, "gaia chat") {
		t.Fatalf("the screen tells the user to type a CLI command:\n%s", v)
	}
}

func TestStatsErrorsAreShown(t *testing.T) {
	f := backend()
	f.StatsErr = errors.New("the routing stats name a scope this app doesn't know (\"agent_ui\")")
	if v := view(open(t, f)); !strings.Contains(v, "doesn't know") {
		t.Fatalf("stats error:\n%s", v)
	}
}

func TestViewFitsSmallWindows(t *testing.T) {
	m := open(t, backend())
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

func TestRouteLine(t *testing.T) {
	d := 2
	for _, tc := range []struct {
		r    event.CanonicalRoute
		want string
	}{
		{event.CanonicalRoute{Model: "amd.Claude-Opus-5-5", Kind: "change_code", Difficulty: &d, Stakes: "high", Classified: true},
			"→ Opus (code change, d2, high stakes)"},
		{event.CanonicalRoute{Model: "claude-haiku-5-5", Sticky: true}, "→ Haiku (kept on it after an earlier hard turn)"},
		{event.CanonicalRoute{Model: "fireworks.accounts/fireworks/models/deepseek-v4p1-flash", Kind: "brand_new", Classified: true},
			"→ Deepseek (brand_new)"},
	} {
		if got := RouteLine(tc.r); got != tc.want {
			t.Errorf("got %q want %q", got, tc.want)
		}
	}
}

func TestASaveAndATemplateApplyNeverRace(t *testing.T) {
	m := open(t, backend())
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(Model)
	if !m.applying {
		t.Fatal("enter did not start applying")
	}
	m.policy = "balanced"
	if _, cmd := m.save(); cmd != nil {
		t.Fatal("ctrl+s sent a save while a template was applying")
	}
	m.applying, m.saving = false, true
	m.focus = 0
	if _, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter}); cmd != nil {
		t.Fatal("enter applied a template while a save was in flight")
	}
}

// A saved limit with a fraction fits and reads as unchanged.
func TestADecimalLimitIsShownWhole(t *testing.T) {
	f := backend()
	f.View.ClassifierTimeout = "100.5"
	m := open(t, f)
	if m.timeout.Value() != "100.5" || m.dirty() {
		t.Fatalf("timeout %q, dirty %v", m.timeout.Value(), m.dirty())
	}
}

func TestPresetKeysComeFromTheBackendsPresets(t *testing.T) {
	m := focus(t, open(t, backend()), rowPolicy)
	if h := m.hint(); !strings.Contains(h, "q/b start from Quality/Balanced") {
		t.Fatalf("hint %q", h)
	}
}
