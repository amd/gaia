// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/sessionstore"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/sessions"
)

// sessionChat is a flagship chat that keeps sessions in a temp store, on a
// binary that resolves but is never run (the client spawns lazily).
func sessionChat(t *testing.T) (FlagshipModel, string) {
	t.Helper()
	m, _ := liveChatModel(t, catalog.FlagshipID)
	agent := fixtureAgent(t, catalog.FlagshipID)
	agent.CanonicalEvents = true
	m.agent = agent
	dir := t.TempDir()
	m = m.WithSessionsDir(dir)
	m.width, m.height = 100, 30
	updated, _ := m.launchAgent(agent, true)
	return updated.(FlagshipModel), dir
}

func seedSession(t *testing.T, dir, id, title string, open bool, turns ...sessionstore.Turn) sessionstore.Session {
	t.Helper()
	meta, _ := json.Marshal(map[string]any{
		"version": 1, "id": id, "title": title, "kind": "chat", "status": "done",
		"created_at": 1.0, "updated_at": 2.0, "pid": 0, "turns": len(turns),
	})
	if err := os.WriteFile(filepath.Join(dir, id+".json"), meta, 0o600); err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, turn := range turns {
		raw, _ := json.Marshal(turn)
		lines = append(lines, string(raw))
	}
	if err := os.WriteFile(filepath.Join(dir, id+".jsonl"), []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s, err := sessionstore.Get(dir, id, func(string) (bool, error) { return open, nil })
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func update(t *testing.T, m FlagshipModel, msg tea.Msg) FlagshipModel {
	t.Helper()
	next, _ := m.Update(msg)
	return next.(FlagshipModel)
}

func lastStatus(m FlagshipModel) string {
	msgs := m.chat.Messages()
	for i := len(msgs) - 1; i >= 0; i-- {
		if msgs[i].Role == chat.RoleStatus {
			return msgs[i].Content
		}
	}
	return ""
}

func TestEveryFlagshipLaunchIsAListedSession(t *testing.T) {
	m, dir := sessionChat(t)
	if m.SessionID() == "" {
		t.Fatal("the flagship launched without a session to come back to")
	}
	_, file, err := m.sessionLaunch(m.agent)
	if err != nil || filepath.Dir(file) != dir {
		t.Fatalf("history file %q, %v; want one in %s", file, err, dir)
	}
}

func TestAnotherAgentKeepsNoSession(t *testing.T) {
	m, _ := sessionChat(t)
	updated, _ := m.launchAgent(fixtureAgent(t, "other"), true)
	if got := updated.(FlagshipModel).SessionID(); got != "" {
		t.Fatalf("agent other was given session %q", got)
	}
}

func TestSessionsCommandOpensTheListAndEscReturnsToChat(t *testing.T) {
	m, _ := sessionChat(t)
	m = update(t, m, chat.OpenSessionsMsg{})
	if m.activeView != viewSessions || m.ControlSnapshot().Overlay != "sessions" {
		t.Fatalf("view %v overlay %q", m.activeView, m.ControlSnapshot().Overlay)
	}
	m = update(t, m, sessions.ClosedMsg{})
	if m.activeView != viewChat || m.sessionsPanel != nil {
		t.Fatalf("esc left view %v", m.activeView)
	}
}

func TestPickingASessionRedrawsItAndRestartsTheAgentOnIt(t *testing.T) {
	m, dir := sessionChat(t)
	before := m.SessionID()
	s := seedSession(t, dir, "abcdef01", "Plan the offsite", false,
		sessionstore.Turn{User: "Plan the offsite", Answer: "Here is a plan."},
		sessionstore.Turn{User: "Book it", Error: "Lemonade is not running"})

	m = update(t, m, chat.OpenSessionsMsg{})
	m = update(t, m, sessions.SelectedMsg{Session: s})

	if m.SessionID() != "abcdef01" || m.SessionID() == before {
		t.Fatalf("session = %q, want abcdef01", m.SessionID())
	}
	if m.activeView != viewChat {
		t.Fatalf("view = %v", m.activeView)
	}
	var got []string
	for _, msg := range m.chat.Messages() {
		got = append(got, string(msg.Role)+":"+msg.Content)
	}
	want := []string{
		"user:Plan the offsite", "assistant:Here is a plan.",
		"user:Book it", "error:Lemonade is not running",
	}
	if len(got) < len(want)+1 || strings.Join(got[:4], "|") != strings.Join(want, "|") {
		t.Fatalf("transcript = %q", got)
	}
	if !strings.Contains(got[4], `Reopened "Plan the offsite"`) {
		t.Fatalf("no resume note: %q", got[4])
	}
}

func TestASessionOpenElsewhereIsNotReopened(t *testing.T) {
	m, dir := sessionChat(t)
	before := m.SessionID()
	s := seedSession(t, dir, "abcdef02", "Busy", true, sessionstore.Turn{User: "q", Answer: "a"})
	if !s.Live {
		t.Fatal("test setup: session should read as live")
	}
	m = update(t, m, sessions.SelectedMsg{Session: s})
	if m.SessionID() != before || !strings.Contains(lastStatus(m), "open in another GAIA window") {
		t.Fatalf("session %q status %q", m.SessionID(), lastStatus(m))
	}
}

func TestPickingTheCurrentSessionChangesNothing(t *testing.T) {
	m, _ := sessionChat(t)
	m = update(t, m, sessions.SelectedMsg{Session: sessionstore.Session{ID: m.SessionID()}})
	if !strings.Contains(lastStatus(m), "session you are in") {
		t.Fatalf("status %q", lastStatus(m))
	}
}

func TestADaemonAgentSaysItKeepsNoSessions(t *testing.T) {
	m, _ := liveChatModel(t, "email")
	m = update(t, m, chat.OpenSessionsMsg{})
	if m.activeView != viewChat || !strings.Contains(lastStatus(m), "does not keep sessions") {
		t.Fatalf("view %v status %q", m.activeView, lastStatus(m))
	}
}

// pickFirst is a bare --resume just past the gate: the list is up, and no
// chat exists yet.
func pickFirst(t *testing.T) (FlagshipModel, string) {
	t.Helper()
	m, _ := liveChatModel(t, catalog.FlagshipID)
	dir := t.TempDir()
	m = m.WithSessionsDir(dir).WithSessionPicker()
	m.chat = nil
	m.width, m.height = 100, 30
	agent := fixtureAgent(t, catalog.FlagshipID)
	agent.CanonicalEvents = true
	updated, _ := m.launchOrPick(agent, true)
	return updated.(FlagshipModel), dir
}

func TestBareResumeShowsTheListBeforeAnyChat(t *testing.T) {
	m, _ := pickFirst(t)
	if m.activeView != viewSessions || m.chat != nil || m.SessionID() != "" {
		t.Fatalf("view %v chat %v session %q: the list must come before the chat", m.activeView, m.chat != nil, m.SessionID())
	}
}

func TestBareResumeEscStartsAFreshSession(t *testing.T) {
	m, _ := pickFirst(t)
	m = update(t, m, sessions.ClosedMsg{})
	if m.activeView != viewChat || m.chat == nil || m.SessionID() == "" {
		t.Fatalf("view %v session %q", m.activeView, m.SessionID())
	}
}

func TestBareResumePickOpensOnlyThePickedSession(t *testing.T) {
	m, dir := pickFirst(t)
	s := seedSession(t, dir, "abcdef04", "Earlier", false, sessionstore.Turn{User: "hi", Answer: "hello"})
	m = update(t, m, sessions.SelectedMsg{Session: s})
	if m.SessionID() != "abcdef04" || m.chat.Messages()[1].Content != "hello" {
		t.Fatalf("session %q messages %+v", m.SessionID(), m.chat.Messages())
	}
}

func TestBareResumeOpenElsewhereSaysSoInTheFreshChat(t *testing.T) {
	m, dir := pickFirst(t)
	s := seedSession(t, dir, "abcdef05", "Busy", true, sessionstore.Turn{User: "q", Answer: "a"})
	m = update(t, m, sessions.SelectedMsg{Session: s})
	if m.chat == nil || m.SessionID() == "abcdef05" {
		t.Fatalf("session %q", m.SessionID())
	}
	found := false
	for _, msg := range m.chat.Messages() {
		found = found || strings.Contains(msg.Content, "open in another GAIA window")
	}
	if !found {
		t.Fatalf("the refusal never reached the chat: %+v", m.chat.Messages())
	}
}

// Whatever is not input keeps reaching the chat while the list covers it; a
// resize is the observable stand-in for a turn's events and spinner ticks.
func TestTheChatKeepsRunningBehindTheList(t *testing.T) {
	m, _ := sessionChat(t)
	m = update(t, m, tea.WindowSizeMsg{Width: 100, Height: 30})
	m = update(t, m, chat.OpenSessionsMsg{})
	before := m.chat.ControlSnapshot().Chat.ViewportRows
	m = update(t, m, tea.WindowSizeMsg{Width: 100, Height: 20})
	if after := m.chat.ControlSnapshot().Chat.ViewportRows; after == before {
		t.Fatalf("the chat behind the list never saw the resize (viewport %d rows)", after)
	}
	m = update(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("x")})
	if m.activeView != viewSessions || strings.Contains(m.chat.View(), "› x") {
		t.Fatal("a key meant for the list reached the chat")
	}
}

func TestResumeWithAnIDLaunchesOnThatSession(t *testing.T) {
	m, _ := liveChatModel(t, catalog.FlagshipID)
	dir := t.TempDir()
	s := seedSession(t, dir, "abcdef03", "Earlier", false, sessionstore.Turn{User: "hi", Answer: "hello"})
	turns, err := sessionstore.Transcript(dir, s.ID)
	if err != nil {
		t.Fatal(err)
	}
	m = m.WithSessionsDir(dir).WithResume(s, turns)
	m.chat = nil
	updated, _ := m.launchAgent(fixtureAgent(t, catalog.FlagshipID), true)
	got := updated.(FlagshipModel)
	if got.SessionID() != "abcdef03" || got.chat.Messages()[1].Content != "hello" {
		t.Fatalf("session %q messages %+v", got.SessionID(), got.chat.Messages())
	}
}
