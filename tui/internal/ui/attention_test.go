// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"bytes"
	"errors"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/attention"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// stubScreen is a root model whose attention state the test sets directly.
type stubScreen struct {
	state attention.State
	chat  bool
	seen  *[]tea.Msg
}

type setState struct{ s attention.State }

func (s stubScreen) Init() tea.Cmd { return nil }
func (s stubScreen) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	*s.seen = append(*s.seen, msg)
	if m, ok := msg.(setState); ok {
		s.state = m.s
	}
	return s, nil
}
func (s stubScreen) View() string                       { return "" }
func (s stubScreen) Attention() (attention.State, bool) { return s.state, s.chat }

func newTestWatch(inChat bool) (attentionWatch, *bytes.Buffer, *[]tea.Msg) {
	var out bytes.Buffer
	seen := &[]tea.Msg{}
	mon := attention.NewMonitor(attention.Defaults(), attention.StyleOSC777, &out, nil, nil)
	return attentionWatch{inner: stubScreen{chat: inChat, seen: seen}, w: &watch{mon: mon}}, &out, seen
}

func step(t *testing.T, a attentionWatch, msg tea.Msg) (attentionWatch, tea.Cmd) {
	t.Helper()
	next, cmd := a.Update(msg)
	return next.(attentionWatch), cmd
}

var askApproval = attention.State{Working: true, UserTurn: true, Ask: attention.AskApproval, AskKey: "r", AskText: "Delete 3 files"}

func TestBlurredTerminalGetsThePermissionNotification(t *testing.T) {
	a, out, seen := newTestWatch(true)
	a, _ = step(t, a, tea.BlurMsg{})
	a, _ = step(t, a, setState{attention.State{Working: true, UserTurn: true}})
	_, _ = step(t, a, setState{askApproval})
	want := "\x1b]777;notify;GAIA needs your approval;Delete 3 files\a"
	if out.String() != want {
		t.Fatalf("got %q, want %q", out.String(), want)
	}
	// The focus message still reaches the screen underneath.
	if _, ok := (*seen)[0].(tea.BlurMsg); !ok {
		t.Fatalf("inner model saw %T first", (*seen)[0])
	}
}

func TestFocusedTerminalGetsNoNotification(t *testing.T) {
	a, out, _ := newTestWatch(true)
	a, _ = step(t, a, tea.BlurMsg{})
	a, _ = step(t, a, tea.FocusMsg{})
	a, _ = step(t, a, setState{attention.State{Working: true, UserTurn: true}})
	a, _ = step(t, a, setState{askApproval})
	_, _ = step(t, a, setState{attention.State{}})
	if out.Len() != 0 {
		t.Fatalf("got %q", out.String())
	}
}

func TestKeepAwakeFailureIsShownOnceInTheChat(t *testing.T) {
	a, _, seen := newTestWatch(false)
	a, cmd := step(t, a, keepAwakeFailedMsg{err: errors.New("systemd-inhibit was not found")})
	if cmd != nil {
		t.Fatal("a note was sent while no conversation was on screen")
	}
	if len(*seen) != 0 {
		t.Fatalf("the failure leaked to the screen as %v", *seen)
	}

	inner := a.inner.(stubScreen)
	inner.chat = true
	a.inner = inner
	a, cmd = step(t, a, setState{})
	notes := drain(cmd)
	if len(notes) != 1 {
		t.Fatalf("got %d notes, want 1: %v", len(notes), notes)
	}
	if want := "systemd-inhibit was not found"; !bytes.Contains([]byte(notes[0].Text), []byte(want)) ||
		!bytes.Contains([]byte(notes[0].Text), []byte("keep_awake false")) {
		t.Fatalf("note %q must say what failed and how to turn it off", notes[0].Text)
	}
	if _, cmd = step(t, a, setState{}); len(drain(cmd)) != 0 {
		t.Fatal("the note was shown twice")
	}
}

// drain runs cmd and collects the notices it carries.
func drain(cmd tea.Cmd) []chat.NoticeMsg {
	if cmd == nil {
		return nil
	}
	var out []chat.NoticeMsg
	switch msg := cmd().(type) {
	case chat.NoticeMsg:
		out = append(out, msg)
	case tea.BatchMsg:
		for _, c := range msg {
			out = append(out, drain(c)...)
		}
	}
	return out
}
