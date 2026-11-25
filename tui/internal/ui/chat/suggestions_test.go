// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
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

// reportSkills is the agent's startup ping, naming the skills it has.
func reportSkills(names ...string) event.CanonicalStatusEvent {
	skills := []event.SkillCommand{}
	for _, n := range names {
		skills = append(skills, event.SkillCommand{Name: n, Description: n + " skill."})
	}
	return event.CanonicalStatusEvent{Type: "status", ModelID: "m", ModelDisplay: "M", Skills: skills}
}

// A normal install has no skills folder: the starters are built into the agent,
// which names them in its startup ping.
func TestSkillsBuiltIntoTheAgentAreSuggested(t *testing.T) {
	m := NewChatModelForFlagship(&nullClient{}, "gaia", "gaia", true)
	m.width, m.height = 100, 30
	m.resize()
	if len(m.starters) != 0 || strings.Contains(screen(m), "Try one") {
		t.Fatalf("suggestions before the agent reported any skill: %+v", m.starters)
	}
	m = feed(t, m, reportSkills(starterPack...))
	got := screen(m)
	for _, want := range []string{"Try one", "1  Today's tech news in brief", "4  Book recommendations"} {
		if !strings.Contains(got, want) {
			t.Fatalf("first screen lacks %q:\n%s", want, got)
		}
	}
	next, _ := sendStarter(m, 3)
	if picked := next.(ChatModel); !picked.streaming || picked.messages[0].Content != starters[2].prompt {
		t.Errorf("selecting the third starter did not send it: %+v", picked.messages)
	}
	// They are still there for the next new chat.
	cleared, _ := m.submit("/clear")
	if got := screen(cleared.(ChatModel)); !strings.Contains(got, "Try one") {
		t.Errorf("/clear lost the suggestions:\n%s", got)
	}
}

// The agent's report is the truth: a skill it does not have is not offered,
// whatever the skills folder holds.
func TestTheAgentsReportReplacesTheFolder(t *testing.T) {
	dir := installSkills(t, "daily-brief", "check-in")
	old := skillsDir
	skillsDir = func() string { return dir }
	t.Cleanup(func() { skillsDir = old })
	m := NewChatModelForFlagship(&nullClient{}, "gaia", "gaia", true)
	m = feed(t, m, reportSkills("check-in", "summarize"))
	if len(m.starters) != 1 || m.starters[0].skill != "check-in" {
		t.Errorf("after the report, starters = %+v", m.starters)
	}
	// A status line that says nothing about skills changes nothing.
	m = feed(t, m, event.CanonicalStatusEvent{Type: "status", Message: "Working"})
	if len(m.starters) != 1 {
		t.Errorf("a plain status line dropped the suggestions: %+v", m.starters)
	}
	// An agent running without skills offers none.
	m = feed(t, m, reportSkills())
	if len(m.starters) != 0 {
		t.Errorf("an agent with no skills still suggests %+v", m.starters)
	}
}

// skillListingClient is the daemon relay: its agent has to be asked.
type skillListingClient struct {
	nullClient
	skills []event.SkillCommand
	err    error
}

func (c *skillListingClient) ListSkills(context.Context) ([]event.SkillCommand, error) {
	return c.skills, c.err
}

// Over the daemon relay no startup ping carries the skills, so the chat asks.
func TestTheRelayIsAskedForItsSkills(t *testing.T) {
	c := &skillListingClient{skills: reportSkills(starterPack...).Skills}
	m := NewChatModelForFlagship(c, "gaia", "gaia", true)
	m.width, m.height = 100, 30
	m.resize()
	cmd := m.listSkillsCmd()
	if cmd == nil {
		t.Fatal("a transport that lists skills was not asked")
	}
	next, _ := m.Update(cmd())
	got := next.(ChatModel)
	if view := ansi.Strip(got.View()); !strings.Contains(view, "Try one") || !strings.Contains(view, "2  Research a topic, with sources") {
		t.Fatalf("the answer did not reach the first screen:\n%s", view)
	}
	if len(got.skillCommands) != len(starterPack) {
		t.Errorf("the / list has %d skills, want %d", len(got.skillCommands), len(starterPack))
	}

	// A failed listing keeps what the skills folder gave.
	before := got.starters
	next, _ = got.Update(skillsListedMsg{err: errors.New("relay down")})
	if after := next.(ChatModel).starters; len(after) != len(before) {
		t.Errorf("a failed listing changed the suggestions: %+v", after)
	}

	// A transport whose agent reports by itself is not asked.
	if NewChatModelForFlagship(&nullClient{}, "gaia", "gaia", true).listSkillsCmd() != nil {
		t.Error("the subprocess transport was asked for its skills")
	}
}

func TestOnlyTheFlagshipSuggests(t *testing.T) {
	m := NewChatModelForCatalogAgent(&nullClient{}, "notes", "notes")
	m = feed(t, m, reportSkills(starterPack...))
	if len(m.starters) != 0 {
		t.Errorf("a catalog agent suggests %+v", m.starters)
	}
}

func TestSkillsFolderFollowsGaiaHome(t *testing.T) {
	home, cfg := t.TempDir(), t.TempDir()
	t.Setenv("GAIA_HOME", home)
	t.Setenv("GAIA_CONFIG_DIR", cfg)
	if got, want := userSkillsDir(), filepath.Join(home, "skills"); got != want {
		t.Errorf("with GAIA_HOME set: %q, want %q", got, want)
	}
	t.Setenv("GAIA_HOME", " ")
	if got, want := userSkillsDir(), filepath.Join(cfg, "skills"); got != want {
		t.Errorf("with only GAIA_CONFIG_DIR set: %q, want %q", got, want)
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

func focusStarter(m ChatModel, row int) ChatModel {
	for range row {
		next, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyTab})
		m = next.(ChatModel)
	}
	return m
}

func sendStarter(m ChatModel, row int) (tea.Model, tea.Cmd) {
	return focusStarter(m, row).handleKey(tea.KeyMsg{Type: tea.KeyEnter})
}

func TestPickingASuggestionStartsThatTask(t *testing.T) {
	for i, starter := range suggestingChat(t, 100, 30).starters {
		m := focusStarter(*suggestingChat(t, 100, 30), i+1)
		if m.streaming || m.starterFocus != i+1 || !strings.Contains(screen(m), "> "+string('1'+rune(i))+"  "+starter.label) {
			t.Fatalf("Tab should mark row %d without starting a turn", i+1)
		}
		next, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
		got := next.(ChatModel)
		if cmd == nil || !got.streaming || len(got.messages) != 1 || got.messages[0].Content != starter.prompt {
			t.Errorf("Enter on row %d sent %+v, want %q", i+1, got.messages, starter.prompt)
		}
		if got.input.Value() != "" || got.starterFocus != 0 {
			t.Errorf("sending kept input %q or starter focus %d", got.input.Value(), got.starterFocus)
		}
	}

}

func TestFirstDigitsAreTypedWithVisibleStarters(t *testing.T) {
	for _, state := range []string{"welcome", "ready"} {
		t.Run(state, func(t *testing.T) {
			for digit := '1'; digit <= '4'; digit++ {
				m := suggestingChat(t, 100, 30)
				if state == "ready" {
					m.warming, m.streaming = true, true
					m.finishWarmUp(client.WarmedUp)
				}
				if !m.canFocusStarter() {
					t.Fatal("fixture has no visible starters")
				}
				next, _ := m.handleKey(digitKey(digit))
				if got := next.(ChatModel); got.streaming || got.hasUserMessage() || got.input.Value() != string(digit) {
					t.Errorf("%c started a turn instead of being typed: input %q", digit, got.input.Value())
				}
			}
		})
	}
}

func TestStarterFocusLeavesTheComposerKeysAlone(t *testing.T) {
	m := focusStarter(*suggestingChat(t, 100, 30), 2)
	next, _ := m.handleKey(digitKey('1'))
	got := next.(ChatModel)
	if got.starterFocus != 0 || got.input.Value() != "1" || got.streaming {
		t.Fatal("typing while a starter is focused must return to the composer")
	}
	next, _ = got.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if submitted := next.(ChatModel); !submitted.streaming || submitted.messages[0].Content != "1" {
		t.Errorf("Enter must send the typed text, got %+v", submitted.messages)
	}
	for _, key := range []tea.KeyType{tea.KeyEsc, tea.KeyShiftTab} {
		m = focusStarter(*suggestingChat(t, 100, 30), 1)
		next, _ = m.handleKey(tea.KeyMsg{Type: key})
		if dismissed := next.(ChatModel); dismissed.starterFocus != 0 || dismissed.streaming {
			t.Errorf("%v did not leave starter focus", key)
		}
	}
	if got := focusStarter(*suggestingChat(t, 100, 30), suggestionsMax+1); got.starterFocus != 0 || got.streaming {
		t.Error("Tab beyond the last starter did not return to the composer")
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
	if got := focusStarter(*m, 1); got.starterFocus != 0 || got.streaming {
		t.Error("Tab selected a starter after the conversation began")
	}
}

// A notice that replaces the welcome screen hides the list and clears any
// prior focus; Tab and Enter cannot start a hidden suggestion.
func TestHiddenSuggestionsAreNotPicked(t *testing.T) {
	m := focusStarter(*suggestingChat(t, 100, 30), 1)
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: discordOfferText()})
	m.updateViewport()
	if strings.Contains(screen(m), "Try one") {
		t.Fatal("the notice was expected to replace the welcome screen")
	}
	next, cmd := sendStarter(m, 1)
	if got := next.(ChatModel); cmd != nil || got.streaming || got.starterFocus != 0 {
		t.Fatal("Tab and Enter must not run a hidden starter")
	}
	next, _ = m.handleKey(digitKey('1'))
	if got := next.(ChatModel); got.streaming || got.input.Value() != "1" {
		t.Errorf("1 with no list on screen: input %q streaming=%v", got.input.Value(), got.streaming)
	}
}

// A local model's warm-up ends with a Ready line that replaces the welcome
// screen; the suggestions move into it and stay pickable.
func TestSuggestionsSurviveTheWarmUp(t *testing.T) {
	m := suggestingChat(t, 100, 30)
	m.warming, m.streaming = true, true
	m.finishWarmUp(client.WarmedUp)
	got := screen(*m)
	if !strings.Contains(got, "Ready") || !strings.Contains(got, starterHint) || !strings.Contains(got, "1 Today's tech news in brief") {
		t.Fatalf("the Ready line lost the suggestions:\n%s", got)
	}
	if strings.Contains(got, firstPromptHint[:20]) {
		t.Errorf("both the old hint and the suggestions are shown:\n%s", got)
	}
	focused := focusStarter(*m, 1)
	if got := screen(focused); !strings.Contains(got, "> 1 Today's tech news in brief") {
		t.Fatalf("the focused starter wasn't marked in the Ready line:\n%s", got)
	}
	next, _ := focused.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if got := next.(ChatModel); !got.streaming || got.messages[len(got.messages)-1].Content != starters[0].prompt {
		t.Error("Tab and Enter after warm-up did not start the first suggestion")
	}
}

// Over the relay the skill list can arrive after the warm-up ends: the Ready
// line was written without it, and must gain the list the user can pick from.
func TestSkillsReportedAfterTheWarmUpReachTheReadyLine(t *testing.T) {
	m := NewChatModelForFlagship(&nullClient{}, "gaia", "gaia", true)
	m.width, m.height = 100, 30
	m.resize()
	m.warming, m.streaming = true, true
	m.finishWarmUp(client.WarmedUp)
	if got := screen(m); !strings.Contains(got, firstPromptHint[:20]) || strings.Contains(got, "Try one") {
		t.Fatalf("before the report the Ready line should carry the plain hint:\n%s", got)
	}
	next, _ := m.Update(skillsListedMsg{skills: reportSkills(starterPack...).Skills})
	m = next.(ChatModel)
	got := screen(m)
	if !strings.Contains(got, "Ready") || !strings.Contains(got, "1 Today's tech news in brief") || strings.Contains(got, firstPromptHint[:20]) {
		t.Fatalf("the late report did not reach the Ready line:\n%s", got)
	}
	picked, _ := sendStarter(m, 1)
	if p := picked.(ChatModel); !p.streaming || p.messages[len(p.messages)-1].Content != starters[0].prompt {
		t.Error("Tab and Enter did not start the first suggestion after a late report")
	}

	// A report that changes the list clears stale focus, even on the same row.
	m = focusStarter(m, 1)
	next, _ = m.Update(skillsListedMsg{skills: reportSkills("check-in").Skills})
	m = next.(ChatModel)
	if got := screen(m); !strings.Contains(got, "1 A daily check-in") || strings.Contains(got, "tech news") {
		t.Fatalf("the Ready line kept a stale list:\n%s", got)
	}
	picked, _ = m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	if p := picked.(ChatModel); p.streaming || p.starterFocus != 0 {
		t.Fatal("Enter after a changed list reused stale focus")
	}
	picked, _ = sendStarter(m, 1)
	if p := picked.(ChatModel); p.messages[len(p.messages)-1].Content != starters[4].prompt {
		t.Error("Tab and Enter did not follow the rewritten list")
	}

	// Once the conversation has started, the Ready line is history.
	m.messages = append(m.messages, Message{Role: RoleUser, Content: "hi"})
	before := m.messages[0].Content
	next, _ = m.Update(skillsListedMsg{skills: reportSkills(starterPack...).Skills})
	if after := next.(ChatModel).messages[0].Content; after != before {
		t.Errorf("a report after the first message rewrote the transcript: %q", after)
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
