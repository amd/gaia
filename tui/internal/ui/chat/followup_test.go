// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// followUpClient is a transport that can hand a running turn a message.
type followUpClient struct {
	nullClient
	supported bool
	sent      []string
	err       error
}

func (c *followUpClient) FollowUpSupported() bool { return c.supported }

func (c *followUpClient) SendFollowUp(_ context.Context, text string) error {
	if c.err != nil {
		return c.err
	}
	c.sent = append(c.sent, text)
	return nil
}

func newFollowUpChat(t *testing.T, c *followUpClient) ChatModel {
	t.Helper()
	m := NewChatModel(c, "GAIA", "", false)
	m.width, m.height = 100, 30
	m.resize()
	m.streaming = true
	return m
}

// run executes the Cmd a keypress returned and feeds its message back, the way
// Bubble Tea's own loop does. Without this the delivery never settles and the
// model is left mid-exchange, which is not a state a user ever sees.
//
// Batches are unrolled rather than run as one message: the top-level Update
// wrapper can fold a mouse-mode Cmd in beside the one under test, and a
// tea.BatchMsg handed back to Update is not a message the model knows.
func run(t *testing.T, m ChatModel, cmd tea.Cmd) ChatModel {
	t.Helper()
	if cmd == nil {
		return m
	}
	switch msg := cmd().(type) {
	case nil:
		return m
	case tea.BatchMsg:
		for _, sub := range msg {
			m = run(t, m, sub)
		}
		return m
	default:
		updated, _ := m.Update(msg)
		return updated.(ChatModel)
	}
}

// The whole point of the feature: a second thought during a long turn reaches
// the agent NOW, on the turn already running, instead of waiting it out.
func TestEnterMidTurnSendsToTheRunningTurn(t *testing.T) {
	c := &followUpClient{supported: true}
	m := typeInto(t, newFollowUpChat(t, c), "also check my calendar")

	m, cmd := press(t, m, tea.KeyEnter)
	if len(m.queued) != 0 {
		t.Fatalf("a deliverable follow-up was parked in the local queue instead: %q", m.queued)
	}
	if !queuedIs(m.sending, "also check my calendar") {
		t.Fatalf("the message is not recorded as in flight: %q", m.sending)
	}

	m = run(t, m, cmd)
	if len(c.sent) != 1 || c.sent[0] != "also check my calendar" {
		t.Fatalf("the follow-up never reached the transport: %q", c.sent)
	}
	if len(m.sending) != 0 {
		t.Errorf("a delivered message is still marked in flight: %q", m.sending)
	}
	if !m.streaming {
		t.Error("delivering a follow-up must not disturb the turn it was sent to")
	}
}

// "Sent" has to mean sent. The transcript line is the user's only evidence the
// agent has their words, so it must not appear until the sidecar took them.
func TestTheTranscriptLineWaitsForTheDelivery(t *testing.T) {
	c := &followUpClient{supported: true}
	m := typeInto(t, newFollowUpChat(t, c), "and the calendar")

	m, cmd := press(t, m, tea.KeyEnter)
	for _, msg := range m.messages {
		if msg.Role == RoleUser {
			t.Fatalf("the message was posted before it was delivered: %+v", m.messages)
		}
	}

	m = run(t, m, cmd)
	var posted bool
	for _, msg := range m.messages {
		if msg.Role == RoleUser && msg.Content == "and the calendar" {
			posted = true
		}
	}
	if !posted {
		t.Errorf("a delivered follow-up never reached the transcript: %+v", m.messages)
	}
}

// A refused delivery must leave the message somewhere the user can still see
// it go out — silence here looks exactly like the message being eaten.
func TestARefusedFollowUpFallsBackToTheQueue(t *testing.T) {
	c := &followUpClient{supported: true, err: errors.New("the run ended before the follow-up reached it")}
	m := typeInto(t, newFollowUpChat(t, c), "and the calendar")

	m, cmd := press(t, m, tea.KeyEnter)
	m = run(t, m, cmd)

	if !queuedIs(m.queued, "and the calendar") {
		t.Fatalf("an undelivered message was dropped instead of queued: %q", m.queued)
	}
	if len(m.sending) != 0 {
		t.Errorf("a settled delivery is still marked in flight: %q", m.sending)
	}
	var told bool
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "the run ended") {
			told = true
		}
	}
	if !told {
		t.Errorf("the failure was not reported to the user: %+v", m.messages)
	}
}

// Esc is the commonest way a delivery ends up refused. Answering a cancel by
// firing the message as a fresh turn is the opposite of what Esc asked for, so
// it goes back to the composer instead — where Esc puts everything else.
func TestAFollowUpRefusedAfterTheTurnEndedGoesBackToTheComposer(t *testing.T) {
	c := &followUpClient{supported: true, err: errors.New("no live run to take a follow-up")}
	m := typeInto(t, newFollowUpChat(t, c), "and the calendar")

	m, cmd := press(t, m, tea.KeyEnter)
	m.streaming = false // the turn settled while the POST was in flight
	m = run(t, m, cmd)

	if len(m.queued) != 0 {
		t.Fatalf("the message was queued, so it fires as a new turn the user did not ask for: %q", m.queued)
	}
	if got := m.input.Value(); got != "and the calendar" {
		t.Errorf("the message was not put back in the composer: %q", got)
	}
}

// /clear mid-turn means "clear when this turn is over", and the agent has no
// use for the literal word folded into its context.
func TestSlashCommandsAreNeverSentMidTurn(t *testing.T) {
	c := &followUpClient{supported: true}
	m := typeInto(t, newFollowUpChat(t, c), "/clear")

	m, _ = press(t, m, tea.KeyEnter)
	if !queuedIs(m.queued, "/clear") {
		t.Fatalf("a slash command did not take the local queue: %q", m.queued)
	}
	if len(c.sent) != 0 {
		t.Errorf("a slash command was sent to the agent as text: %q", c.sent)
	}
}

// An older sidecar has no endpoint for this. Claiming the message went to the
// running turn when it 404s would be a lie the user only discovers by waiting.
func TestAnOlderPeerKeepsTheLocalQueue(t *testing.T) {
	c := &followUpClient{supported: false}
	m := typeInto(t, newFollowUpChat(t, c), "and the calendar")

	m, _ = press(t, m, tea.KeyEnter)
	if !queuedIs(m.queued, "and the calendar") {
		t.Fatalf("a peer that cannot take mid-turn input did not fall back to the queue: %q", m.queued)
	}
	if len(c.sent) != 0 {
		t.Errorf("a follow-up was sent to a peer that cannot take one: %q", c.sent)
	}
}

// The composer row is the only place the user learns which of the two things
// Enter is about to do, and they are not the same thing.
func TestTheComposerSaysWhichEnterThisIs(t *testing.T) {
	deliverable := typeInto(t, newFollowUpChat(t, &followUpClient{supported: true}), "and the calendar")
	if got := deliverable.midTurnEnterHint(); !strings.Contains(got, "running turn") {
		t.Errorf("a deliverable Enter still advertises queueing: %q", got)
	}

	old := typeInto(t, newFollowUpChat(t, &followUpClient{supported: false}), "and the calendar")
	if got := old.midTurnEnterHint(); got != "⏎ queues" {
		t.Errorf("an undeliverable Enter must not promise the running turn: %q", got)
	}
}

// In flight is a state the user can see, not a silent gap between pressing
// Enter and the line appearing.
func TestTheRowShowsAMessageInFlight(t *testing.T) {
	m := newFollowUpChat(t, &followUpClient{supported: true})
	m.sending = []string{"and the calendar"}

	row := ansi.Strip(m.renderQueuedRow())
	if !strings.Contains(row, "sending") || !strings.Contains(row, "and the calendar") {
		t.Errorf("the in-flight message is not shown: %q", row)
	}
}

// Bubble Tea runs Update on one goroutine, so every transcript rebuild is time
// the composer is not reading keys. Tokens arrive faster than a screen
// refreshes; rebuilding per token is what makes typing feel dead mid-answer.
func TestStreamedTokensDoNotRebuildTheTranscriptEachTime(t *testing.T) {
	m := newTestChat(t)
	m = feed(t, m,
		event.CanonicalTokenEvent{Type: "token", Delta: "one "},
		event.CanonicalTokenEvent{Type: "token", Delta: "two "},
		event.CanonicalTokenEvent{Type: "token", Delta: "three"},
	)
	if !m.viewDirty {
		t.Error("every token rebuilt the whole transcript; the composer competes with that work")
	}

	// Held, never lost: the repaint tick that runs all turn puts them up.
	m = repaint(t, m)
	if m.viewDirty {
		t.Fatal("the repaint tick did not flush the held tokens")
	}
	if out := ansi.Strip(m.viewport.View()); !strings.Contains(out, "one two three") {
		t.Errorf("held tokens never reached the screen:\n%s", out)
	}
}

// Typing while an answer streams is the case the throttle exists for, so it is
// the case worth asserting end to end.
func TestKeystrokesSurviveAFastTokenStream(t *testing.T) {
	m := newTestChat(t)
	for i := 0; i < 50; i++ {
		m = feed(t, m, event.CanonicalTokenEvent{Type: "token", Delta: "tok "})
		m = typeInto(t, m, "x")
	}
	if got := m.input.Value(); got != strings.Repeat("x", 50) {
		t.Errorf("keystrokes were lost under a streaming answer: %q", got)
	}
}
