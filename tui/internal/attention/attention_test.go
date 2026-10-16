// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"bytes"
	"errors"
	"strings"
	"testing"
)

type fakeHolder struct{ calls []bool }

func (f *fakeHolder) Hold(on bool) { f.calls = append(f.calls, on) }

func newTestMonitor(s Settings, style Style) (*Monitor, *bytes.Buffer, *fakeHolder) {
	var out bytes.Buffer
	h := &fakeHolder{}
	return NewMonitor(s, style, &out, h, nil), &out, h
}

var (
	idle     = State{}
	working  = State{Working: true, UserTurn: true}
	approval = State{Working: true, UserTurn: true, Ask: AskApproval, AskKey: "run1", AskText: "Run `rm -rf build`"}
	question = State{Working: true, UserTurn: true, Ask: AskQuestion, AskKey: "q1", AskText: "Which file?"}
)

func observeAll(t *testing.T, m *Monitor, states ...State) {
	t.Helper()
	for _, s := range states {
		if err := m.Observe(s); err != nil {
			t.Fatalf("Observe(%+v): %v", s, err)
		}
	}
}

func TestApprovalNotifiesOnlyWhileUnfocused(t *testing.T) {
	m, out, _ := newTestMonitor(Defaults(), StyleOSC777)
	m.SetFocus(true)
	observeAll(t, m, idle, working, approval)
	if out.Len() != 0 {
		t.Fatalf("focused terminal got a notification: %q", out.String())
	}

	m2, out2, _ := newTestMonitor(Defaults(), StyleOSC777)
	m2.SetFocus(false)
	observeAll(t, m2, idle, working, approval)
	want := "\x1b]777;notify;GAIA needs your approval;Run `rm -rf build`\a"
	if out2.String() != want {
		t.Fatalf("got %q, want %q", out2.String(), want)
	}
}

func TestNoFocusReportCountsAsFocused(t *testing.T) {
	m, out, _ := newTestMonitor(Defaults(), StyleBell)
	observeAll(t, m, idle, working, approval, idle)
	if out.Len() != 0 {
		t.Fatalf("a terminal that never reported focus got %q", out.String())
	}
}

func TestRefocusStopsNotifications(t *testing.T) {
	m, out, _ := newTestMonitor(Defaults(), StyleBell)
	m.SetFocus(false)
	m.SetFocus(true)
	observeAll(t, m, idle, working, question, idle)
	if out.Len() != 0 {
		t.Fatalf("got %q after focus came back", out.String())
	}
}

func TestProbeOverridesFocusReports(t *testing.T) {
	var out bytes.Buffer
	focused, known := false, true
	m := NewMonitor(Defaults(), StyleBell, &out, nil, func() (bool, bool) { return focused, known })
	m.SetFocus(true) // ignored while the probe knows
	observeAll(t, m, idle, working, approval)
	if out.String() != "\a" {
		t.Fatalf("probe says background: got %q, want one bell", out.String())
	}

	out.Reset()
	focused = true
	observeAll(t, m, idle, working, question)
	if out.Len() != 0 {
		t.Fatalf("probe says in front: got %q", out.String())
	}

	observeAll(t, m, idle)
	out.Reset()
	known = false
	m.SetFocus(false)
	observeAll(t, m, working, approval)
	if out.String() != "\a" {
		t.Fatalf("probe unsure, terminal reported blur: got %q, want one bell", out.String())
	}
}

func TestEachPromptNotifiesOnce(t *testing.T) {
	m, out, _ := newTestMonitor(Defaults(), StyleBell)
	m.SetFocus(false)
	observeAll(t, m, idle, working, approval, approval, approval)
	if out.String() != "\a" {
		t.Fatalf("one prompt on screen for three updates: got %q", out.String())
	}
	next := approval
	next.AskKey = "run2"
	observeAll(t, m, next)
	if out.String() != "\a\a" {
		t.Fatalf("a second prompt: got %q, want two bells", out.String())
	}
}

func TestFinishedFiresForUserTurnsOnly(t *testing.T) {
	m, out, _ := newTestMonitor(Defaults(), StyleOSC9)
	m.SetFocus(false)
	warmup := State{Working: true}
	observeAll(t, m, idle, warmup, idle)
	if out.Len() != 0 {
		t.Fatalf("the warm-up finishing notified: %q", out.String())
	}
	observeAll(t, m, working, idle)
	want := "\x1b]9;GAIA is done: It finished working and is waiting for you.\a"
	if out.String() != want {
		t.Fatalf("got %q, want %q", out.String(), want)
	}
}

func TestPerEventSettings(t *testing.T) {
	cases := []struct {
		name   string
		s      Settings
		states []State
	}{
		{"approval off", Settings{Question: true, Finished: true}, []State{idle, working, approval}},
		{"question off", Settings{Approval: true, Finished: true}, []State{idle, working, question}},
		{"finished off", Settings{Approval: true, Question: true}, []State{idle, working, idle}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m, out, _ := newTestMonitor(tc.s, StyleBell)
			m.SetFocus(false)
			observeAll(t, m, tc.states...)
			if out.Len() != 0 {
				t.Fatalf("event switched off still notified: %q", out.String())
			}
		})
	}
	// And each one alone is enough.
	for _, tc := range []struct {
		s      Settings
		states []State
	}{
		{Settings{Approval: true}, []State{idle, working, approval}},
		{Settings{Question: true}, []State{idle, working, question}},
		{Settings{Finished: true}, []State{idle, working, idle}},
	} {
		m, out, _ := newTestMonitor(tc.s, StyleBell)
		m.SetFocus(false)
		observeAll(t, m, tc.states...)
		if out.String() != "\a" {
			t.Fatalf("%+v: got %q, want one bell", tc.s, out.String())
		}
	}
}

func TestKeepAwakeFollowsTheTurn(t *testing.T) {
	m, _, h := newTestMonitor(Defaults(), StyleBell)
	// The hold drops while a prompt waits on the user and comes back after.
	observeAll(t, m, idle, working, approval, working, idle, idle, working)
	m.Close()
	want := []bool{true, false, true, false, true, false}
	if len(h.calls) != len(want) {
		t.Fatalf("holds %v, want %v", h.calls, want)
	}
	for i := range want {
		if h.calls[i] != want[i] {
			t.Fatalf("holds %v, want %v", h.calls, want)
		}
	}
}

func TestKeepAwakeWorksWithoutNotifications(t *testing.T) {
	m, out, h := newTestMonitor(Settings{KeepAwake: true}, StyleBell)
	m.SetFocus(false)
	observeAll(t, m, idle, working, idle)
	if out.Len() != 0 || len(h.calls) != 2 {
		t.Fatalf("out %q holds %v", out.String(), h.calls)
	}
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("closed") }

func TestWriteFailureIsReported(t *testing.T) {
	m := NewMonitor(Defaults(), StyleBell, failingWriter{}, nil, nil)
	m.SetFocus(false)
	_ = m.Observe(working)
	err := m.Observe(approval)
	if err == nil || !strings.Contains(err.Error(), "desktop notification") {
		t.Fatalf("got %v, want a plain-words write failure", err)
	}
}
