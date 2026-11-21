// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// ── The version the running agent reports ───────────────────────────────

type versionedClient struct {
	nullClient
	version string
}

func (v *versionedClient) AgentVersion() string { return v.version }

// Whatever the agent puts in its ping, the header stays one row inside the
// terminal and never shows control bytes or a doubled "vv".
func TestHostileReportedVersionsKeepTheHeaderOneCleanRow(t *testing.T) {
	cases := map[string]string{
		"empty":         "",
		"spaces":        "   ",
		"leading v":     "v0.3.0",
		"newline":       "0.3.0\nINJECTED",
		"ansi":          "0.3.0\x1b[31mRED\x1b[0m",
		"bell+tab":      "0.3.0\a\t",
		"very long":     strings.Repeat("9", 5000),
		"cjk":           "版本一",
		"invalid utf-8": "0.3\xff\xfe",
	}
	for name, version := range cases {
		t.Run(name, func(t *testing.T) {
			m := NewChatModelForFlagship(&nullClient{}, "gaia", "GAIA", true)
			m.width, m.height = 80, 24
			m = feed(t, m, versionedPing(version, version))

			header := m.renderHeader()
			plain := ansi.Strip(header)
			if strings.ContainsAny(plain, "\n\r\a\t\x1b") {
				t.Fatalf("header carries control bytes: %q", plain)
			}
			if w := ansi.StringWidth(header); w > m.width {
				t.Fatalf("header is %d wide on an %d-column terminal", w, m.width)
			}
			if strings.Contains(plain, " vv") {
				t.Errorf("header doubled the v: %q", plain)
			}
			if !utf8.ValidString(plain) {
				t.Errorf("header is not valid UTF-8: %q", plain)
			}
			welcome := m.renderWelcome()
			if strings.ContainsAny(ansi.Strip(welcome), "\r\a\t\x1b") ||
				strings.Count(welcome, "\n") != 3 {
				t.Errorf("the version broke the welcome's lines: %q", ansi.Strip(welcome))
			}
			id := m.agentIdentity()
			if utf8.RuneCountInString(id) > 80 {
				t.Errorf("identity is %d runes long", utf8.RuneCountInString(id))
			}
			if strings.TrimSpace(version) == "" && strings.Contains(id, " v") {
				t.Errorf("a blank version invented one: %q", id)
			}
		})
	}
}

// The HTTP peer's own probe wins over the ping, but a blank probe does not
// erase a version the ping did report.
func TestLiveProbeBeatsThePingButABlankProbeDoesNot(t *testing.T) {
	c := &versionedClient{version: "0.9.0"}
	m := NewChatModelForFlagship(c, "gaia", "GAIA", true)
	m = feed(t, m, versionedPing("0.3.0", ""))
	if got := m.agentIdentity(); got != "agent gaia v0.9.0" {
		t.Errorf("identity = %q, want the probe's 0.9.0", got)
	}
	c.version = ""
	if got := m.agentIdentity(); got != "agent gaia v0.3.0" {
		t.Errorf("identity = %q, want the ping's 0.3.0 once the probe is blank", got)
	}
}

// ── Word delete at the edges ─────────────────────────────────────────────

func TestWordDeleteAtTheStartOfTheComposerIsANoOp(t *testing.T) {
	for _, k := range []tea.KeyMsg{{Type: tea.KeyCtrlH}, {Type: tea.KeyCtrlW}} {
		empty := pressKey(t, newTestChat(t), k)
		if got := empty.input.Value(); got != "" {
			t.Errorf("%s on an empty composer left %q", k, got)
		}
		m := typeInto(t, newTestChat(t), "keep all of this")
		m.input.CursorStart()
		m = pressKey(t, m, k)
		if got := m.input.Value(); got != "keep all of this" {
			t.Errorf("%s at the start changed the text to %q", k, got)
		}
	}
	m := typeInto(t, newTestChat(t), "keep all of this")
	m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyCtrlDelete})
	if got := m.input.Value(); got != "keep all of this" {
		t.Errorf("ctrl+delete at the end changed the text to %q", got)
	}
}

func TestWordDeleteNeverSplitsAMultiByteCharacter(t *testing.T) {
	for _, text := range []string{
		"总结 这份报告",
		"ship it 👍🏽",
		"family 👨‍👩‍👧 photo",
		"café crème",
		"日本語テキスト",
	} {
		t.Run(text, func(t *testing.T) {
			m := typeInto(t, newTestChat(t), text)
			for i := 0; i < 10 && m.input.Value() != ""; i++ {
				before := m.input.Value()
				m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyCtrlH})
				after := m.input.Value()
				if !utf8.ValidString(after) {
					t.Fatalf("ctrl+backspace left invalid UTF-8: %q", after)
				}
				if !strings.HasPrefix(before, after) || len(after) >= len(before) {
					t.Fatalf("ctrl+backspace turned %q into %q", before, after)
				}
			}
			if m.input.Value() != "" {
				t.Errorf("ten word deletes left %q", m.input.Value())
			}

			m = typeInto(t, newTestChat(t), text)
			m.input.CursorStart()
			for i := 0; i < 10 && m.input.Value() != ""; i++ {
				before := m.input.Value()
				m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyCtrlDelete})
				after := m.input.Value()
				if !utf8.ValidString(after) || !strings.HasSuffix(before, after) {
					t.Fatalf("ctrl+delete turned %q into %q", before, after)
				}
			}
		})
	}
}

func TestWordDeleteAcrossWhitespaceRuns(t *testing.T) {
	m := typeInto(t, newTestChat(t), "alpha    beta     ")
	m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyCtrlH})
	if got := m.input.Value(); got != "alpha    " {
		t.Errorf("ctrl+backspace over trailing spaces left %q, want the word and its spaces gone", got)
	}
	m = typeInto(t, newTestChat(t), "     ")
	m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyCtrlH})
	if got := m.input.Value(); got != "" {
		t.Errorf("ctrl+backspace on only spaces left %q", got)
	}
}

func TestHoldingCtrlBackspaceEmptiesALongComposerWithoutPanicking(t *testing.T) {
	m := typeInto(t, newTestChat(t), strings.Repeat("word ", 200))
	for i := 0; i < 400; i++ {
		m = pressKey(t, m, tea.KeyMsg{Type: tea.KeyCtrlH})
	}
	if got := m.input.Value(); got != "" {
		t.Errorf("400 word deletes left %q", got)
	}
}

// ── Plan headline and narration dedupe ───────────────────────────────────

func TestInterleavedHeadlinesAreEachKeptAndNeverMultiplied(t *testing.T) {
	m := newTestChat(t)
	m.expandWork = true
	// Real goals interleave and each heads its own step; the plan runner's
	// "Following the plan to <request>" is a template and is never shown.
	a, b := "Compare the term sheets", "Draft the summary"
	for step := 1; step <= 4; step++ {
		goal := a
		if step%2 == 0 {
			goal = b
		}
		m = feed(t, m,
			event.CanonicalStatusEvent{Type: "status", Message: fmt.Sprintf("Executing step %d of the plan", step)},
			event.CanonicalStatusEvent{Type: "status", Message: "Following the plan to " + goal},
			event.CanonicalStatusEvent{Type: "status", Message: goal},
			event.CanonicalToolCallEvent{Type: "tool_call", Tool: "read_file"},
			event.CanonicalToolResultEvent{Type: "tool_result", Tool: "read_file", Preview: "ok"},
		)
	}
	out := ansi.Strip(m.renderWork(&Message{Role: RoleWork, Work: m.activity}))
	for _, h := range []string{a, b} {
		if n := strings.Count(out, "● "+h); n != 2 {
			t.Errorf("%q heads %d steps, want 2:\n%s", h, n, out)
		}
		if n := strings.Count(out, h); n != 2 {
			t.Errorf("%q is said %d times, want once per step:\n%s", h, n, out)
		}
	}
	for _, noise := range []string{"Following the plan", "Executing step"} {
		if strings.Contains(out, noise) {
			t.Errorf("plan template %q reached the screen:\n%s", noise, out)
		}
	}
}

func TestSameNarrationSeparatedByOtherNarrationIsNotDropped(t *testing.T) {
	m := newTestChat(t)
	m.expandWork = true
	for _, note := range []string{"Reading the file.", "Checking totals.", "Reading the file."} {
		m.buffer = note
		m.stashNarration()
	}
	var notes []string
	for _, item := range m.activity {
		if item.Kind == "note" {
			notes = append(notes, item.Content)
		}
	}
	if len(notes) != 3 {
		t.Errorf("notes = %q, want all three: only back-to-back repeats collapse", notes)
	}
}

func TestBackToBackIdenticalNarrationCollapsesEvenWhenLong(t *testing.T) {
	m := newTestChat(t)
	long := strings.Repeat("very long narration ", 200)
	for i := 0; i < 5; i++ {
		m.buffer = long
		m.stashNarration()
	}
	n := 0
	for _, item := range m.activity {
		if item.Kind == "note" {
			n++
		}
	}
	if n != 1 {
		t.Errorf("five identical long notes kept as %d", n)
	}
}

func TestStageReturnWithNoEarlierStageIsKept(t *testing.T) {
	m := newTestChat(t)
	m.setLiveStatus("Only stage")
	m.setLiveStatus("Second stage")
	m.setLiveStatus("Only stage")
	got := 0
	for _, item := range m.activity {
		if item.Kind == "status" {
			got++
		}
	}
	if got != 1 || m.activity[len(m.activity)-1].Content != "Only stage" {
		t.Errorf("activity = %+v, want one stage line reading the latest stage", m.activity)
	}
}
