// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// A follow-up drained from the queue (#2917) runs as its own turn, with
// nobody watching it the way a user watches a turn they just pressed Enter
// on. If it stalls, queuedSendStalledMsg is what tells them — this is the
// only place that message is handled, so it is tested directly rather than
// via the 180s real timer that schedules it.
func TestQueuedSendStalledMsgNamesTheStall(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true

	updated, _ := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	var named bool
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "queued follow-up") {
			named = true
		}
	}
	if !named {
		t.Errorf("a stalled queued send must be named, not left silent: %+v", m.messages)
	}
	// Purely informative -- the turn may still complete, so nothing here may
	// pull the rug out from under it. The user's own Esc/Ctrl+C is the exit.
	if !m.streaming {
		t.Error("the stall notice must not itself end the turn")
	}
}

// A late queuedSendStalledMsg -- the turn it was scheduled for already
// settled, or a newer turn is now live -- must be a silent no-op. Otherwise a
// slow, since-finished queued send would leave a confusing "still running"
// notice sitting under an answer that already arrived.
func TestStaleQueuedSendStalledMsgIsIgnored(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 2
	m.streaming = true
	before := len(m.messages)

	updated, _ := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	if len(m.messages) != before {
		t.Errorf("a stale stall notice must not be shown: %+v", m.messages)
	}
}

// Same guard when the matching turn already stopped streaming (settled via
// doneMsg/errMsg/a terminal event) even though its turnSeq is still current.
func TestQueuedSendStalledMsgIgnoredOnceSettled(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = false
	before := len(m.messages)

	updated, _ := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	if len(m.messages) != before {
		t.Errorf("a settled turn must not get a stall notice: %+v", m.messages)
	}
}

// A queued follow-up that is still sending updates is working, however long
// it has run: no stall notice, and the watchdog re-arms for the next quiet spell.
func TestQueuedSendStalledMsgIsQuietWhileTheTurnKeepsUpdating(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true
	ch := make(chan interface{})
	m.events = ch
	before := len(m.messages)

	updated, _ := m.Update(eventMsg{ch: ch, event: event.CanonicalStatusEvent{Type: "status", Message: "Reading the inbox"}})
	m = updated.(ChatModel)
	updated, cmd := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	for _, msg := range m.messages[before:] {
		if strings.Contains(msg.Content, "queued follow-up") {
			t.Fatalf("a turn that just sent an update must not be called stalled: %q", msg.Content)
		}
	}
	if cmd == nil {
		t.Error("the watchdog must re-arm so a later silence is still named")
	}
}

// The likely cause of a long silence on a shared machine is a turn waiting for
// the model, not a broken daemon, so the notice says so.
func TestQueuedSendStalledNoticeNamesTheModelWait(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true

	updated, _ := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	last := m.messages[len(m.messages)-1].Content
	if !strings.Contains(last, "waiting its turn for the model") {
		t.Errorf("the notice must name the model wait: %q", last)
	}
	if strings.Contains(last, "gaia daemon status") {
		t.Errorf("the notice must not send the user to the daemon for a model wait: %q", last)
	}
}

// A turn parked on a permission prompt is waiting on the user, not stalled.
func TestQueuedSendStalledMsgWaitsOutAPendingPrompt(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true
	cm := components.NewConfirmationModel("run", "write_file", "Write?", "")
	m.confirmation = &cm
	before := len(m.messages)

	updated, cmd := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	if len(m.messages) != before {
		t.Errorf("a turn waiting on the user must not be called stalled: %+v", m.messages[before:])
	}
	if cmd == nil {
		t.Error("the watchdog must re-arm for after the prompt is answered")
	}
}
