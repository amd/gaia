// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
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
