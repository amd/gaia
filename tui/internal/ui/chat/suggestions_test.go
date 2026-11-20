// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
)

// installSkills lays out a skills folder the way `gaia init` leaves it.
func installSkills(t *testing.T, names ...string) string {
	t.Helper()
	dir := t.TempDir()
	for _, n := range names {
		writeFile(t, filepath.Join(dir, n, "SKILL.md"), "---\nname: "+n+"\n---\n")
	}
	return dir
}

// starterPack is what a fresh install has in ~/.gaia/skills.
var starterPack = []string{"check-in", "coding", "daily-brief", "data-explore", "document-brief",
	"file-ops", "github-triage", "image-gen", "inbox-triage", "price-watch", "recommendations",
	"research-report", "source-watch", "summarize", "transcribe-meeting"}

func TestFreshInstallSuggestsSkillsThatNeedNoSetup(t *testing.T) {
	got := installedStarters(installSkills(t, starterPack...))
	var names []string
	for _, s := range got {
		names = append(names, s.skill)
	}
	if want := "daily-brief,research-report,file-ops,recommendations"; strings.Join(names, ",") != want {
		t.Errorf("suggested %v, want %s", names, want)
	}
	// A skill that is not installed is never offered.
	got = installedStarters(installSkills(t, "check-in", "summarize"))
	if len(got) != 1 || got[0].skill != "check-in" {
		t.Errorf("with only check-in installed, got %+v", got)
	}
	if installedStarters(t.TempDir()) != nil || installedStarters("") != nil {
		t.Error("no skills should mean no suggestions")
	}
}

func TestFlagshipReadsTheSkillsFolder(t *testing.T) {
	dir := installSkills(t, "daily-brief")
	old := skillsDir
	skillsDir = func() string { return dir }
	t.Cleanup(func() { skillsDir = old })
	m := NewChatModelForFlagship(&nullClient{}, "gaia", "gaia", true)
	if len(m.starters) != 1 {
		t.Errorf("flagship starters = %+v", m.starters)
	}
}

func suggestingChat(t *testing.T, w, h int) *ChatModel {
	t.Helper()
	m, _ := frameChat(t, w, h)
	m.starters = installedStarters(installSkills(t, starterPack...))
	m.updateViewport()
	return m
}

func digitKey(r rune) tea.KeyMsg { return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}} }

func TestPickingASuggestionStartsThatTask(t *testing.T) {
	m := suggestingChat(t, 100, 30)
	next, cmd := m.handleKey(digitKey('2'))
	got := next.(ChatModel)
	if cmd == nil || !got.streaming {
		t.Fatal("picking a suggestion did not start a turn")
	}
	if len(got.messages) == 0 || got.messages[0].Content != starters[1].prompt {
		t.Errorf("sent %+v, want %q", got.messages, starters[1].prompt)
	}
	if got.input.Value() != "" {
		t.Errorf("the composer kept the prompt: %q", got.input.Value())
	}
}

func TestDigitsAreTextOnceTheUserIsTyping(t *testing.T) {
	m := suggestingChat(t, 100, 30)
	m.input.SetValue("Top ")
	next, _ := m.handleKey(digitKey('3'))
	if got := next.(ChatModel); got.streaming || got.input.Value() != "Top 3" {
		t.Errorf("a digit mid-message should be typed, got %q streaming=%v", got.input.Value(), got.streaming)
	}
	// A number past the list is a digit too.
	m2 := suggestingChat(t, 100, 30)
	next, _ = m2.handleKey(digitKey('7'))
	if got := next.(ChatModel); got.streaming || got.input.Value() != "7" {
		t.Errorf("7 with four suggestions: %q streaming=%v", got.input.Value(), got.streaming)
	}
}

func TestSuggestionsLeaveOnceTheConversationStarts(t *testing.T) {
	m := suggestingChat(t, 100, 30)
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "hi"})
	if strings.Contains(screen(*m), "Try one") {
		t.Error("suggestions outlived the first message")
	}
	if _, _, ok := m.pickSuggestion(digitKey('1')); ok {
		t.Error("a number picked a suggestion that is no longer on screen")
	}
}

// A local model's warm-up ends with a Ready line that replaces the welcome
// screen; the suggestions move into it and stay pickable.
func TestSuggestionsSurviveTheWarmUp(t *testing.T) {
	m := suggestingChat(t, 100, 30)
	m.warming, m.streaming = true, true
	m.finishWarmUp(client.WarmedUp)
	got := screen(*m)
	if !strings.Contains(got, "Ready") || !strings.Contains(got, "press its number") || !strings.Contains(got, "1 Today's tech news in brief") {
		t.Fatalf("the Ready line lost the suggestions:\n%s", got)
	}
	if strings.Contains(got, firstPromptHint[:20]) {
		t.Errorf("both the old hint and the suggestions are shown:\n%s", got)
	}
	next, _ := m.handleKey(digitKey('1'))
	if got := next.(ChatModel); !got.streaming || got.messages[len(got.messages)-1].Content != starters[0].prompt {
		t.Errorf("1 after warm-up did not start the first suggestion")
	}
}

func TestFrameFirstScreen(t *testing.T) {
	for _, tc := range []struct {
		name string
		w, h int
	}{{"first-screen", 100, 20}, {"first-screen-40", 40, 20}} {
		t.Run(tc.name, func(t *testing.T) {
			m := suggestingChat(t, tc.w, tc.h)
			got := screen(*m)
			golden(t, tc.name, got)
			if nc := noColorView(t, *m, "Try one", "  1  "); nc != got {
				t.Errorf("the NO_COLOR frame differs:\n%s", nc)
			}
			for i, row := range strings.Split(got, "\n") {
				if w := ansi.StringWidth(row); w > tc.w {
					t.Errorf("row %d is %d columns wide: %q", i, w, row)
				}
			}
		})
	}
}
