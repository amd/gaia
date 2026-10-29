// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

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

// With no word from the agent about a queue, a long silence could be a wait
// for the model or a stuck daemon, so the notice keeps the diagnostic step.
func TestQueuedSendStalledNoticeKeepsTheDiagnosticWithoutAQueueSignal(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true

	updated, _ := m.Update(queuedSendStalledMsg{turnSeq: 1})
	m = updated.(ChatModel)

	last := m.messages[len(m.messages)-1].Content
	if !strings.Contains(last, "no update for 3m0s") || !strings.Contains(last, "gaia daemon status") {
		t.Errorf("the notice must say what it saw and keep the next step: %q", last)
	}
	if strings.Contains(last, "most likely") {
		t.Errorf("the notice must not guess at a cause it was never told: %q", last)
	}
}

// A turn queued for the model hears an update every couple of seconds, so it
// is never quiet long enough to be called stalled.
func TestQueueUpdatesKeepAQueuedFollowUpFromBeingCalledStalled(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true
	ch := make(chan interface{})
	m.events = ch
	queued := event.CanonicalStatusEvent{Type: "status", Phase: "model_queue",
		Message: "Waiting for the model — gaia-chat is using it, 2nd in line (40s)"}
	updated, _ := m.Update(eventMsg{ch: ch, event: queued})
	m = updated.(ChatModel)

	if stalled, _ := m.queuedStallCheck(time.Now().Add(2 * time.Second)); stalled {
		t.Error("a follow-up that just heard a queue update was called stalled")
	}
}

// Silence after the model was granted is not a queue wait: the notice keeps
// the diagnostic step instead of reporting the last queue line.
func TestASilenceAfterTheModelWasGrantedKeepsTheDiagnostic(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true
	ch := make(chan interface{})
	m.events = ch
	granted := event.CanonicalStatusEvent{Type: "status", Phase: "model_queue", Message: "Got the model after waiting 12s"}
	updated, _ := m.Update(eventMsg{ch: ch, event: granted})
	m = updated.(ChatModel)

	later := time.Now().Add(queuedSendTimeout + time.Second)
	if stalled, _ := m.queuedStallCheck(later); !stalled {
		t.Fatal("three minutes of silence after the grant should be named")
	}
	notice := m.queuedStallNotice()
	if !strings.Contains(notice, "gaia daemon status") {
		t.Errorf("the notice must keep the diagnostic step: %q", notice)
	}
	if strings.Contains(notice, "Got the model") || strings.Contains(notice, "still waiting for the model") {
		t.Errorf("the notice must not report a queue that is over: %q", notice)
	}
}

// The silence is counted from the last event OR the end of the user's own
// wait, so answering a prompt does not trip the notice a moment later.
func TestAnsweringAPromptRestartsTheQuietClock(t *testing.T) {
	m := newTestChat(t)
	m.turnSeq = 1
	m.streaming = true
	start := time.Now()
	m.lastEventAt = start.Add(-10 * time.Minute)
	cm := components.NewConfirmationModel("run", "write_file", "Write?", "")
	m.confirmation = &cm
	m.trackUserWait(start.Add(-9 * time.Minute))
	m.confirmation = nil
	m.trackUserWait(start)

	stalled, recheck := m.queuedStallCheck(start.Add(5 * time.Second))
	if stalled {
		t.Fatal("a turn whose prompt was answered 5 s ago was called stalled")
	}
	if recheck != queuedSendTimeout-5*time.Second {
		t.Errorf("re-check in %v, want the remaining %v", recheck, queuedSendTimeout-5*time.Second)
	}
}

func TestTheStallWatchdogRechecksAfterTheRemainingQuietTime(t *testing.T) {
	m := newTestChat(t)
	now := time.Now()
	m.lastEventAt = now.Add(-100 * time.Second)

	stalled, recheck := m.queuedStallCheck(now)
	if stalled || recheck != 80*time.Second {
		t.Errorf("got stalled=%v recheck=%v, want false and 80s", stalled, recheck)
	}

	cm := components.NewConfirmationModel("run", "write_file", "Write?", "")
	m.confirmation = &cm
	if stalled, recheck = m.queuedStallCheck(now); stalled || recheck != queuedSendTimeout {
		t.Errorf("while a prompt is open: stalled=%v recheck=%v, want false and the full %v", stalled, recheck, queuedSendTimeout)
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
