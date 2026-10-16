// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "github.com/amd/gaia/tui/internal/attention"

// NoticeMsg puts a one-line status note in the transcript, for parts of the
// program outside the chat view that need to tell the user something.
type NoticeMsg struct{ Text string }

// Attention is what the attention monitor watches: whether a turn runs, and
// whether the agent is waiting on the user. A chat always has an answer.
func (m ChatModel) Attention() (attention.State, bool) {
	s := attention.State{
		Working:  m.streaming,
		UserTurn: m.streaming && !m.warming && !m.startup.inFlight,
	}
	switch {
	case m.confirmation != nil && m.confirmation.Pending():
		c := m.confirmation
		s.Ask = attention.AskApproval
		s.AskKey = c.RunID() + "\x00" + c.Action() + "\x00" + c.Summary()
		// No AskText: a command line can carry a token, and the OS keeps
		// notifications in its history and on the lock screen.
	case m.question != nil:
		s.Ask = attention.AskQuestion
		s.AskKey = m.question.RequestID()
		if !m.question.Sensitive() {
			s.AskText = m.question.Prompt()
		}
	}
	return s, true
}
