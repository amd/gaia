// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// typeInto drives real keystrokes through the model, the way the terminal
// delivers them — not input.SetValue, which would skip the very gate under test.
//
// Resets lastKeyAt once done: a test's whole loop runs faster than the
// pasteBurstWindow guard in handleKey, so without this a real Enter pressed
// right after would be misread as a pasted line break. Typing a sentence and
// then pressing Enter is what this simulates — the two are not simultaneous.
func typeInto(t *testing.T, m ChatModel, s string) ChatModel {
	t.Helper()
	for _, r := range s {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
		m = updated.(ChatModel)
	}
	m.lastKeyAt = time.Time{}
	return m
}

func press(t *testing.T, m ChatModel, k tea.KeyType) (ChatModel, tea.Cmd) {
	t.Helper()
	updated, cmd := m.Update(tea.KeyMsg{Type: k})
	return updated.(ChatModel), cmd
}

// A local model takes 60-120s per turn. A composer that swallows every
// keystroke for that long makes the user hold their next thought in their head.
func TestTheComposerAcceptsTypingWhileTheAgentWorks(t *testing.T) {
	m := typeInto(t, newTestChat(t), "and what about drafts?")

	if got := m.input.Value(); got != "and what about drafts?" {
		t.Fatalf("keystrokes were dropped during a running turn: %q", got)
	}
	if !m.streaming {
		t.Error("typing must not disturb the turn in flight")
	}
}

// Enter mid-turn holds the message; the turn ending releases it.
func TestEnterDuringATurnQueuesAndThenSends(t *testing.T) {
	m := typeInto(t, newTestChat(t), "summarise that")
	m, _ = press(t, m, tea.KeyEnter)

	if m.queued != "summarise that" {
		t.Fatalf("Enter during a turn did not queue the message: %q", m.queued)
	}
	if m.input.Value() != "" {
		t.Error("the composer kept the text it just queued")
	}
	if n := len(m.messages); n != 0 {
		t.Fatalf("a queued message was posted as a turn straight away: %d messages", n)
	}

	// The turn settles; the queue must drain into a real turn.
	updated, _ := m.Update(eventMsg{ch: m.events, event: event.CanonicalFinalEvent{
		Type: "final", Answer: "done",
	}})
	after := updated.(ChatModel)

	if after.queued != "" {
		t.Error("the queue did not drain when the turn ended")
	}
	var sent bool
	for _, msg := range after.messages {
		if msg.Role == RoleUser && msg.Content == "summarise that" {
			sent = true
		}
	}
	if !sent {
		t.Errorf("the queued message never became a turn:\n%+v", after.messages)
	}
	if !after.streaming {
		t.Error("the drained message should have started a new turn")
	}
}

// Retyping replaces rather than stacking — a second Enter means "no, this one".
func TestASecondQueuedMessageReplacesTheFirst(t *testing.T) {
	m := typeInto(t, newTestChat(t), "first")
	m, _ = press(t, m, tea.KeyEnter)
	m = typeInto(t, m, "second")
	m, _ = press(t, m, tea.KeyEnter)

	if m.queued != "second" {
		t.Errorf("queue holds %q; the newer message should win", m.queued)
	}
}

// Esc means "stop". A follow-up written while expecting the turn to finish must
// not fire into the cancel — but it must not vanish either.
func TestCancellingGivesTheQueuedMessageBack(t *testing.T) {
	m := newTestChat(t)
	m.cancelFn = func() {}
	m = typeInto(t, m, "never mind, do this instead")
	m, _ = press(t, m, tea.KeyEnter)
	if m.queued == "" {
		t.Fatal("precondition: nothing queued")
	}

	m, _ = press(t, m, tea.KeyEsc)

	if m.queued != "" {
		t.Error("a cancelled turn still has a message queued behind it")
	}
	if got := m.input.Value(); got != "never mind, do this instead" {
		t.Errorf("the queued sentence was lost on cancel; composer holds %q", got)
	}
}

// A queued slash command has to run as a command, not be sent to the model as a
// literal question.
func TestAQueuedSlashCommandStillRunsAsACommand(t *testing.T) {
	c := &nullClient{}
	m := NewChatModel(c, "email", "", false)
	m.width, m.height = 80, 24
	m.resize()
	m.messages = []Message{{Role: RoleUser, Content: "earlier question"}}
	m.streaming = true

	m = typeInto(t, m, "/clear")
	m, _ = press(t, m, tea.KeyEnter)

	updated, _ := m.Update(doneMsg{ch: m.events})
	after := updated.(ChatModel)

	if c.resets != 1 {
		t.Errorf("the queued /clear did not reset the transcript (resets=%d)", c.resets)
	}
	for _, msg := range after.messages {
		if msg.Role == RoleUser && msg.Content == "/clear" {
			t.Error("/clear was sent to the agent as a question instead of running")
		}
	}
}

// Accepted-but-not-yet-sent is a state the user has to be able to see.
func TestTheQueuedMessageIsVisibleOnScreen(t *testing.T) {
	m := typeInto(t, newTestChat(t), "check the calendar too")
	m, _ = press(t, m, tea.KeyEnter)

	out := ansi.Strip(m.View())
	if !strings.Contains(out, "check the calendar too") {
		t.Errorf("a queued message is invisible, so the user cannot tell it was accepted:\n%s", out)
	}
	if !strings.Contains(out, "queued") {
		t.Errorf("nothing on screen says the message is waiting:\n%s", out)
	}
}

// Mid-sentence the user needs to see their own text, and nothing else on that
// row competing with it.
func TestTheComposerRowShowsTypingInsteadOfTheAgentStatus(t *testing.T) {
	m := typeInto(t, newTestChat(t), "half a thought")

	out := ansi.Strip(m.View())
	if !strings.Contains(out, "half a thought") {
		t.Errorf("the user cannot see what they are typing:\n%s", out)
	}
	if !strings.Contains(out, "⏎ queues") {
		t.Errorf("nothing says what Enter will do with the half-typed line:\n%s", out)
	}
}

// Alt+Enter was swallowed outright — the key a user is most likely to try for
// a second line did nothing whatsoever.
func TestAltEnterStartsANewLineInsteadOfDoingNothing(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m = typeInto(t, m, "first line")

	before := m.viewport.Height
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter, Alt: true})
	m = updated.(ChatModel)
	m = typeInto(t, m, "second line")

	if !strings.Contains(m.input.Value(), "\n") {
		t.Fatalf("Alt+Enter did not insert a newline: %q", m.input.Value())
	}
	if n := len(m.messages); n != 0 {
		t.Errorf("Alt+Enter sent the message instead of adding a line (%d messages)", n)
	}
	if m.input.Height() < 2 {
		t.Errorf("the composer stayed %d row(s) tall with two lines in it", m.input.Height())
	}
	if m.viewport.Height >= before {
		t.Error("the composer grew without giving the transcript back the rows")
	}
}

// Ctrl+J is the portable newline: terminals send a bare CR for Shift+Enter.
func TestCtrlJAlsoStartsANewLine(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m = typeInto(t, m, "one")
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlJ})
	if !strings.Contains(updated.(ChatModel).input.Value(), "\n") {
		t.Error("Ctrl+J did not insert a newline")
	}
}

// A composer that grew has to shrink again, or one long paste permanently
// steals half the window.
func TestTheComposerShrinksBackAfterSending(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	m = typeInto(t, m, "a")
	for i := 0; i < 3; i++ {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlJ})
		m = updated.(ChatModel)
	}
	if m.input.Height() < 2 {
		t.Fatalf("precondition: composer should have grown, got %d", m.input.Height())
	}
	tall := m.viewport.Height

	// This Enter is the deliberate send that ends the test's simulated
	// composing, not another rapid keystroke — same reasoning as typeInto's
	// own reset, needed here because the loop above calls Update directly.
	m.lastKeyAt = time.Time{}
	m, _ = press(t, m, tea.KeyEnter)

	if m.input.Height() != 1 {
		t.Errorf("composer stayed %d rows tall after sending", m.input.Height())
	}
	if m.viewport.Height <= tall {
		t.Error("the transcript never got its rows back")
	}
}

// A pasted essay must not eat the conversation it is about.
func TestTheComposerStopsGrowingAtItsCap(t *testing.T) {
	m := newTestChat(t)
	m.streaming = false
	for i := 0; i < 40; i++ {
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyCtrlJ})
		m = updated.(ChatModel)
	}
	if m.input.Height() > composerMaxRows {
		t.Errorf("composer grew to %d rows; capped at %d", m.input.Height(), composerMaxRows)
	}
	if m.viewport.Height < 1 {
		t.Error("the composer squeezed the transcript out of existence")
	}
}

// Type-ahead nobody knows about might as well not exist.
func TestTheStatusBarAdvertisesTypeAhead(t *testing.T) {
	if out := ansi.Strip(newTestChat(t).View()); !strings.Contains(out, "keep typing") {
		t.Errorf("a busy TUI does not tell the user they can still type:\n%s", out)
	}
}
