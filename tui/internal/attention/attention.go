// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package attention reaches the user when GAIA needs them while they are in
// another window, and keeps the PC awake while a turn runs.
//
// It watches one thing: the chat's State after every update. Edges in that
// state are the events — a prompt appearing, a turn ending — so nothing in the
// chat view has to remember to call it.
package attention

import (
	"fmt"
	"io"
)

// Ask is what the agent is waiting on the user for.
type Ask int

const (
	AskNone Ask = iota
	AskApproval
	AskQuestion
)

// State is the part of the chat that decides whether the user is needed.
type State struct {
	// Working is true while any turn runs. The PC is kept awake for it, except
	// while the turn waits on the user.
	Working bool
	// UserTurn is true while the running turn is one the user asked for, not
	// the start-up warm-up. Only those announce that they finished.
	UserTurn bool
	Ask      Ask
	// AskKey identifies the prompt, so one prompt notifies once however many
	// updates it stays on screen.
	AskKey string
	// AskText is the question, when it is safe to show outside the terminal.
	AskText string
}

// Event is one thing the user can be told about.
type Event int

const (
	EventApproval Event = iota
	EventQuestion
	EventFinished
)

func (e Event) String() string {
	switch e {
	case EventApproval:
		return "approval"
	case EventQuestion:
		return "question"
	}
	return "finished"
}

// Holder keeps the PC awake while held.
type Holder interface {
	Hold(on bool)
}

// Monitor turns State changes into notifications and keep-awake holds. It is
// not safe for concurrent use; Bubble Tea's event loop is its only caller.
type Monitor struct {
	settings Settings
	style    Style
	out      io.Writer
	awake    Holder
	// probe answers "is the terminal in front?" from the OS, where the
	// terminal's focus reports do not reach the program (Windows). known is
	// false when it cannot tell.
	probe func() (focused, known bool)

	// Logf records each decision for the session log; nil logs nothing.
	Logf func(format string, args ...any)

	reported focusReport
	prev     State
	userTurn bool
	holding  bool
}

type focusReport int

const (
	focusUnknown focusReport = iota
	focusIn
	focusOut
)

// NewMonitor builds a monitor writing notifications to out. awake may be nil
// when keep-awake is off; probe may be nil where focus reports suffice.
func NewMonitor(s Settings, style Style, out io.Writer, awake Holder, probe func() (bool, bool)) *Monitor {
	return &Monitor{settings: s, style: style, out: out, awake: awake, probe: probe}
}

// SetFocus records a focus report from the terminal (CSI I / CSI O).
func (m *Monitor) SetFocus(focused bool) {
	if focused {
		m.reported = focusIn
	} else {
		m.reported = focusOut
	}
}

// Focused reports whether the user is looking at the terminal. A terminal
// that never reported its focus counts as focused: with no way to tell, a
// notification would interrupt someone already watching.
func (m *Monitor) Focused() bool {
	if m.probe != nil {
		if focused, known := m.probe(); known {
			return focused
		}
	}
	return m.reported != focusOut
}

// Observe compares s with the last state and acts on what changed. The error
// is a notification that could not be written to the terminal.
func (m *Monitor) Observe(s State) error {
	prev := m.prev
	m.prev = s

	// A prompt left open overnight must not hold the laptop awake.
	if want := s.Working && s.Ask == AskNone; m.awake != nil && want != m.holding {
		m.holding = want
		m.awake.Hold(want)
		m.logf("attention: keep-awake hold=%t", want)
	}

	if s.Working && s.UserTurn {
		m.userTurn = true
	}
	if prev.Working && !s.Working {
		finished := m.userTurn
		m.userTurn = false
		if finished {
			return m.notify(EventFinished, "")
		}
		return nil
	}

	if s.Ask != AskNone && (s.Ask != prev.Ask || s.AskKey != prev.AskKey) {
		if s.Ask == AskApproval {
			return m.notify(EventApproval, s.AskText)
		}
		return m.notify(EventQuestion, s.AskText)
	}
	return nil
}

// Close lets the PC sleep again.
func (m *Monitor) Close() {
	if m.awake != nil && m.holding {
		m.holding = false
		m.awake.Hold(false)
	}
}

func (m *Monitor) notify(e Event, detail string) error {
	switch {
	case !m.settings.Notifies(e):
		m.logf("attention: %s not sent: turned off in settings", e)
		return nil
	case m.Focused():
		m.logf("attention: %s not sent: the terminal is in front", e)
		return nil
	}
	title, body := message(e, detail)
	if _, err := io.WriteString(m.out, Sequence(m.style, title, body)); err != nil {
		return fmt.Errorf("could not send a desktop notification through the terminal: %w", err)
	}
	m.logf("attention: %s sent as %s", e, m.style)
	return nil
}

func (m *Monitor) logf(format string, args ...any) {
	if m.Logf != nil {
		m.Logf(format, args...)
	}
}

func message(e Event, detail string) (title, body string) {
	switch e {
	case EventApproval:
		if detail == "" {
			detail = "A step is waiting for you to allow it."
		}
		return "GAIA needs your approval", detail
	case EventQuestion:
		if detail == "" {
			detail = "GAIA is waiting for your answer."
		}
		return "GAIA has a question", detail
	}
	return "GAIA is done", "It finished working and is waiting for you."
}
