// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "testing"

// A Send that returns after the user abandoned its turn and resent must not
// replace the live turn's event channel; its close would end the new turn.
func TestLateChannelFromAnAbandonedTurnLeavesTheLiveTurnAlone(t *testing.T) {
	m, _ := newTestModel(t)

	updated, _ := m.Update(sendQueryMsg{query: "first"})
	m = updated.(ChatModel)
	staleSeq := m.turnSeq

	// Force-abort, then resend.
	m.streaming = false
	m.cancelFn = nil
	updated, _ = m.Update(sendQueryMsg{query: "second"})
	m = updated.(ChatModel)
	if m.turnSeq == staleSeq {
		t.Fatal("test setup: the resend must start a new turn")
	}

	live := make(chan interface{})
	updated, _ = m.Update(channelReadyMsg{ch: live, turnSeq: m.turnSeq})
	m = updated.(ChatModel)

	stale := make(chan interface{}, 1)
	stale <- "late event"
	close(stale)
	updated, cmd := m.Update(channelReadyMsg{ch: stale, turnSeq: staleSeq})
	m = updated.(ChatModel)

	if m.events != (<-chan interface{})(live) {
		t.Fatal("a late channel from an abandoned turn replaced the live turn's")
	}
	if !m.streaming {
		t.Fatal("a late channel from an abandoned turn ended the live turn")
	}
	if cmd == nil {
		t.Fatal("the stale channel must be drained, not left blocking its producer")
	}
	if msg := cmd(); msg != nil {
		t.Fatalf("draining the stale channel must not message the model, got %#v", msg)
	}
	if len(stale) != 0 {
		t.Fatal("the stale channel was not drained")
	}
}
