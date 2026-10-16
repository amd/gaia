// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"testing"

	"github.com/amd/gaia/tui/internal/attention"
	"github.com/amd/gaia/tui/internal/ui/components"
)

func TestAttentionReportsTheTurnAndItsPrompts(t *testing.T) {
	m, _ := newTestModel(t)
	if s, ok := m.Attention(); !ok || s != (attention.State{}) {
		t.Fatalf("idle chat: %+v %v", s, ok)
	}

	m.streaming = true
	if s, _ := m.Attention(); !s.Working || !s.UserTurn || s.Ask != attention.AskNone {
		t.Fatalf("running turn: %+v", s)
	}

	m.warming = true
	if s, _ := m.Attention(); !s.Working || s.UserTurn {
		t.Fatalf("warm-up must keep awake but not count as the user's turn: %+v", s)
	}
	m.warming = false

	c := components.NewConfirmationModel("run-1", "run_shell", "Run `make build`", "")
	m.confirmation = &c
	s, _ := m.Attention()
	if s.Ask != attention.AskApproval || s.AskKey == "" {
		t.Fatalf("pending approval: %+v", s)
	}
	if s.AskText != "" {
		t.Fatalf("a command line must stay out of the OS notification history: %+v", s)
	}
	m.confirmation = nil

	q := components.NewQuestionModel("q-7", "Which sheet?", nil, true, false)
	m.question = &q
	if s, _ := m.Attention(); s.Ask != attention.AskQuestion || s.AskKey != "q-7" || s.AskText != "Which sheet?" {
		t.Fatalf("question: %+v", s)
	}

	secret := components.NewQuestionModel("q-8", "Paste your token", nil, true, true)
	m.question = &secret
	if s, _ := m.Attention(); s.AskText != "" {
		t.Fatalf("a sensitive question's words must stay out of the notification: %+v", s)
	}
}

func TestNoticeMsgLandsInTheTranscript(t *testing.T) {
	m, _ := newTestModel(t)
	before := len(m.messages)
	updated, _ := m.Update(NoticeMsg{Text: "[!] could not keep the PC awake"})
	got := updated.(ChatModel).messages
	if len(got) != before+1 || got[len(got)-1].Role != RoleStatus || got[len(got)-1].Content != "[!] could not keep the PC awake" {
		t.Fatalf("got %+v", got[before:])
	}
}
