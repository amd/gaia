// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package root

import (
	"context"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/sessionstore"
	"github.com/amd/gaia/tui/internal/ui/chat"
	"github.com/amd/gaia/tui/internal/ui/sessions"
)

// turnClient is an agent whose turns run until cancelled; ctxs receives
// each turn's context so a test can tell whether it was stopped.
type turnClient struct{ ctxs chan context.Context }

func (c *turnClient) Send(ctx context.Context, _ string) (<-chan interface{}, error) {
	c.ctxs <- ctx
	return make(chan interface{}), nil
}
func (c *turnClient) Close() error { return nil }

// collect runs cmd's whole tree concurrently and returns the first message
// keep accepts, or nil after timeout.
func collect(cmd tea.Cmd, timeout time.Duration, keep func(tea.Msg) bool) tea.Msg {
	out := make(chan tea.Msg, 64)
	var run func(tea.Cmd)
	run = func(c tea.Cmd) {
		if c == nil {
			return
		}
		go func() {
			msg := c()
			if batch, ok := msg.(tea.BatchMsg); ok {
				for _, sub := range batch {
					run(sub)
				}
				return
			}
			out <- msg
		}()
	}
	run(cmd)
	deadline := time.After(timeout)
	for {
		select {
		case msg := <-out:
			if keep(msg) {
				return msg
			}
		case <-deadline:
			return nil
		}
	}
}

// midTurn is a session chat with a turn running; the returned context ends
// when that turn is stopped.
func midTurn(t *testing.T, title string) (FlagshipModel, string, context.Context) {
	t.Helper()
	m, dir := sessionChat(t)
	seedSession(t, dir, m.SessionID(), title, true, sessionstore.Turn{User: "hi", Answer: "hello"})
	tc := &turnClient{ctxs: make(chan context.Context, 1)}
	c := chat.NewChatModelForFlagship(tc, m.agent.ID, m.agent.Name, false, true)
	m.chat = &c
	for _, r := range "summarise the report" {
		m = step(t, m, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = next.(FlagshipModel)
	go collect(cmd, 2*time.Second, func(tea.Msg) bool { return false })
	select {
	case ctx := <-tc.ctxs:
		if !m.chat.IsStreaming() {
			t.Fatal("test setup: the turn is not running")
		}
		return m, dir, ctx
	case <-time.After(2 * time.Second):
		t.Fatal("test setup: the turn never reached the agent")
	}
	return m, dir, nil
}

func key(s string) tea.KeyMsg {
	if s == "esc" {
		return tea.KeyMsg{Type: tea.KeyEsc}
	}
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

// press sends a key to the list and delivers the message it answers with.
func press(t *testing.T, m FlagshipModel, k string) FlagshipModel {
	t.Helper()
	next, cmd := m.Update(key(k))
	m = next.(FlagshipModel)
	msg := collect(cmd, time.Second, func(msg tea.Msg) bool {
		switch msg.(type) {
		case sessions.ClosedMsg, sessions.StopAndSwitchMsg:
			return true
		}
		return false
	})
	if msg == nil {
		t.Fatalf("key %q answered nothing", k)
	}
	return step(t, m, msg)
}

func TestPickingASessionMidTurnAsksFirst(t *testing.T) {
	m, dir, turn := midTurn(t, "Draft the memo")
	before := m.SessionID()
	s := seedSession(t, dir, "abcdef11", "Plan the offsite", false, sessionstore.Turn{User: "q", Answer: "a"})

	m = step(t, m, chat.OpenSessionsMsg{})
	m = step(t, m, sessions.SelectedMsg{Session: s})

	if m.activeView != viewSessions || m.sessionsPanel == nil || !m.sessionsPanel.Asking() {
		t.Fatalf("view %v: picking a session mid-turn did not ask first", m.activeView)
	}
	if m.SessionID() != before || turn.Err() != nil || !m.chat.IsStreaming() {
		t.Fatalf("the turn was touched before the user answered (session %q, ctx %v)", m.SessionID(), turn.Err())
	}
	if v := m.View(); !strings.Contains(v, "A turn is still running. Stop it and switch") {
		t.Fatalf("the question is not on screen:\n%s", v)
	}
}

func TestDecliningTheSwitchKeepsTheTurnRunning(t *testing.T) {
	for _, k := range []string{"k", "n", "esc"} {
		t.Run(k, func(t *testing.T) {
			m, dir, turn := midTurn(t, "Draft the memo")
			before := m.SessionID()
			s := seedSession(t, dir, "abcdef12", "Plan the offsite", false, sessionstore.Turn{User: "q", Answer: "a"})
			m = step(t, m, chat.OpenSessionsMsg{})
			m = step(t, m, sessions.SelectedMsg{Session: s})
			m = press(t, m, k)

			if m.activeView != viewChat || m.sessionsPanel != nil {
				t.Fatalf("view %v: declining did not go back to the chat", m.activeView)
			}
			if m.SessionID() != before || turn.Err() != nil || !m.chat.IsStreaming() {
				t.Fatalf("declining stopped or replaced the turn (session %q, ctx %v)", m.SessionID(), turn.Err())
			}
		})
	}
}

func TestConfirmingTheSwitchStopsTheTurnAndSaysSo(t *testing.T) {
	m, dir, turn := midTurn(t, "Draft the memo")
	s := seedSession(t, dir, "abcdef13", "Plan the offsite", false, sessionstore.Turn{User: "q", Answer: "a"})
	m = step(t, m, chat.OpenSessionsMsg{})
	m = step(t, m, sessions.SelectedMsg{Session: s})
	m = press(t, m, "s")

	if m.SessionID() != "abcdef13" || m.activeView != viewChat {
		t.Fatalf("session %q view %v: confirming did not switch", m.SessionID(), m.activeView)
	}
	if turn.Err() == nil {
		t.Fatal("the running turn was never stopped")
	}
	want := `Stopped the turn that was running in "Draft the memo" to reopen "Plan the offsite".`
	if got := lastStatus(m); got != want {
		t.Fatalf("status %q, want %q", got, want)
	}
}

// The turn can finish while the question is up: switching then stops
// nothing, and must not claim it did.
func TestATurnThatFinishedBeforeTheAnswerIsNotReportedStopped(t *testing.T) {
	m, dir, _ := midTurn(t, "Draft the memo")
	s := seedSession(t, dir, "abcdef14", "Plan the offsite", false, sessionstore.Turn{User: "q", Answer: "a"})
	m = step(t, m, chat.OpenSessionsMsg{})
	m = step(t, m, sessions.SelectedMsg{Session: s})
	m.chat.CancelActiveTurn()
	m = press(t, m, "s")

	if m.SessionID() != "abcdef14" {
		t.Fatalf("session %q", m.SessionID())
	}
	for _, msg := range m.chat.Messages() {
		if strings.Contains(msg.Content, "Stopped the turn") {
			t.Fatalf("claimed a turn was stopped: %q", msg.Content)
		}
	}
}

func TestAnIdleChatSwitchesWithoutAsking(t *testing.T) {
	m, dir := sessionChat(t)
	s := seedSession(t, dir, "abcdef15", "Plan the offsite", false, sessionstore.Turn{User: "q", Answer: "a"})
	m = step(t, m, chat.OpenSessionsMsg{})
	m = step(t, m, sessions.SelectedMsg{Session: s})
	if m.SessionID() != "abcdef15" {
		t.Fatalf("an idle chat asked or refused: session %q view %v", m.SessionID(), m.activeView)
	}
}

// leaveSlowly reopens a new session from the current one, whose agent is
// still saving when the switch happens. It returns the session just left.
func leaveSlowly(t *testing.T) (FlagshipModel, string, sessionstore.Session, chan struct{}) {
	t.Helper()
	m, dir := sessionChat(t)
	slow := &slowCloseClient{release: make(chan struct{}), done: make(chan struct{})}
	m.chatClient.set(slow)
	left := m.SessionID()
	seedSession(t, dir, left, "Draft the memo", false, sessionstore.Turn{User: "hi", Answer: "hello"})
	next := seedSession(t, dir, "abcdef16", "Plan the offsite", false, sessionstore.Turn{User: "q", Answer: "a"})
	m = step(t, m, sessions.SelectedMsg{Session: next})
	if m.SessionID() != "abcdef16" {
		t.Fatalf("test setup: session %q", m.SessionID())
	}
	t.Cleanup(func() {
		select {
		case <-slow.release:
		default:
			close(slow.release)
		}
	})
	// The departing agent still holds the lock, so the list reads it live.
	s := seedSession(t, dir, left, "Draft the memo", true, sessionstore.Turn{User: "hi", Answer: "hello"})
	return m, dir, s, slow.release
}

func TestTheSessionJustLeftReopensOnceItHasClosed(t *testing.T) {
	m, _, left, release := leaveSlowly(t)

	next, cmd := m.Update(sessions.SelectedMsg{Session: left})
	m = next.(FlagshipModel)
	status := lastStatus(m)
	if strings.Contains(status, "another GAIA window") || !strings.Contains(status, "still closing") {
		t.Fatalf("status %q: the session this window just left was blamed on another window", status)
	}
	if m.SessionID() != "abcdef16" {
		t.Fatalf("reopened %q before the old agent let go", m.SessionID())
	}

	isClosed := func(msg tea.Msg) bool { _, ok := msg.(sessionClosedMsg); return ok }
	if collect(cmd, 100*time.Millisecond, isClosed) != nil {
		t.Fatal("reported closed while the old agent was still saving")
	}
	close(release)
	msg := collect(cmd, 2*time.Second, isClosed)
	if msg == nil {
		t.Fatal("never heard that the old agent closed")
	}
	m = step(t, m, msg)
	if m.SessionID() != left.ID {
		t.Fatalf("session %q, want the one just left (%s) reopened", m.SessionID(), left.ID)
	}
}

// Moving on while it closes (a new turn, another session) must not be
// overridden by an automatic switch.
func TestAClosedSessionDoesNotPullTheUserBackAfterTheyMovedOn(t *testing.T) {
	m, dir, left, release := leaveSlowly(t)
	next, cmd := m.Update(sessions.SelectedMsg{Session: left})
	m = next.(FlagshipModel)

	other := seedSession(t, dir, "abcdef17", "Something else", false, sessionstore.Turn{User: "q", Answer: "a"})
	m = step(t, m, sessions.SelectedMsg{Session: other})
	close(release)
	msg := collect(cmd, 2*time.Second, func(msg tea.Msg) bool { _, ok := msg.(sessionClosedMsg); return ok })
	if msg == nil {
		t.Fatal("never heard that the old agent closed")
	}
	m = step(t, m, msg)
	if m.SessionID() != "abcdef17" || !strings.Contains(lastStatus(m), "finished closing") {
		t.Fatalf("session %q status %q", m.SessionID(), lastStatus(m))
	}
}

func TestClosingIsForgottenOnceTheAgentExits(t *testing.T) {
	var b clientBox
	slow := &slowCloseClient{release: make(chan struct{}), done: make(chan struct{})}
	b.set(slow)
	b.retire("abcdef18")
	done := b.closingDone("abcdef18")
	if done == nil || b.closingDone("abcdef19") != nil {
		t.Fatal("closingDone does not track the retiring session alone")
	}
	close(slow.release)
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("done never closed")
	}
	b.retiring.Wait()
	if b.closingDone("abcdef18") != nil {
		t.Fatal("a closed session still reads as closing")
	}
}

// A session left, reopened and left again in quick succession has two
// agents closing it; the first one exiting must not clear the second's entry.
func TestAnEarlierCloseDoesNotClearALaterOne(t *testing.T) {
	var b clientBox
	first := &slowCloseClient{release: make(chan struct{}), done: make(chan struct{})}
	second := &slowCloseClient{release: make(chan struct{}), done: make(chan struct{})}
	b.set(first)
	b.retire("abcdef20")
	firstDone := b.closingDone("abcdef20")
	b.set(second)
	b.retire("abcdef20")
	close(first.release)
	<-firstDone
	if b.closingDone("abcdef20") == nil {
		t.Fatal("the second agent still saving no longer reads as closing")
	}
	close(second.release)
	b.retiring.Wait()
}
