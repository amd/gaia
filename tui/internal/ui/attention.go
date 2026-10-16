// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/attention"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// attentionSource is a model that can say whether the user is needed. ok is
// false on screens with no conversation.
type attentionSource interface {
	Attention() (s attention.State, ok bool)
}

// attentionWatch feeds every update's attention state to the monitor, which
// raises notifications and holds the PC awake. Wrapping the root model keeps
// all of that out of the chat view: it sees edges in state, not call sites.
type attentionWatch struct {
	inner tea.Model
	w     *watch
}

// watch is shared by every copy Bubble Tea makes of attentionWatch.
type watch struct {
	mon    *attention.Monitor
	keeper *attention.Keeper
	// notes wait for a conversation to show them in.
	notes       []string
	writeFailed bool
}

type keepAwakeFailedMsg struct{ err error }

// newAttentionWatch reads the user's settings and starts keep-awake. close
// releases any hold; call it once the program has stopped.
func newAttentionWatch(inner tea.Model, out *attention.Terminal) (attentionWatch, func()) {
	w := &watch{}
	path := lemonade.ConfigPathFrom(os.Getenv, os.UserHomeDir)
	settings, err := attention.LoadSettings(path)
	if err != nil {
		w.notes = append(w.notes, fmt.Sprintf(
			"[!] Notification settings could not be read: %v. Fix the file, or run "+
				"`gaia config set <key> true|false`; until then the readable ones apply "+
				"and the rest are on.", err))
	}
	var holder attention.Holder
	if settings.KeepAwake {
		w.keeper = attention.NewKeeper()
		holder = w.keeper
	}
	w.mon = attention.NewMonitor(settings, attention.DetectStyle(os.Getenv), out, holder, attention.ForegroundProbe())
	w.mon.Logf = control.Logf
	return attentionWatch{inner: inner, w: w}, func() {
		w.mon.Close()
		if w.keeper != nil {
			w.keeper.Close()
		}
	}
}

func (a attentionWatch) Init() tea.Cmd {
	return tea.Batch(a.inner.Init(), a.awaitKeepAwakeFailure())
}

// awaitKeepAwakeFailure listens for the keeper's one report.
func (a attentionWatch) awaitKeepAwakeFailure() tea.Cmd {
	if a.w.keeper == nil {
		return nil
	}
	errs := a.w.keeper.Errors()
	return func() tea.Msg { return keepAwakeFailedMsg{err: <-errs} }
}

func (a attentionWatch) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.FocusMsg:
		a.w.mon.SetFocus(true)
	case tea.BlurMsg:
		a.w.mon.SetFocus(false)
	case keepAwakeFailedMsg:
		a.w.notes = append(a.w.notes, fmt.Sprintf(
			"[!] Could not keep this PC awake while GAIA works: %v. If it sleeps, a "+
				"long turn stops. Change the power settings, or turn this off with "+
				"`gaia config set keep_awake false`.", msg.err))
		return a, a.flushNotes()
	}

	next, cmd := a.inner.Update(msg)
	a.inner = next
	src, ok := next.(attentionSource)
	if !ok {
		return a, cmd
	}
	state, ok := src.Attention()
	if !ok {
		return a, cmd
	}
	if err := a.w.mon.Observe(state); err != nil && !a.w.writeFailed {
		a.w.writeFailed = true
		a.w.notes = append(a.w.notes, "[!] "+err.Error())
	}
	return a, tea.Batch(cmd, a.flushNotes())
}

// flushNotes hands waiting notes to the conversation, once one is on screen.
func (a attentionWatch) flushNotes() tea.Cmd {
	if len(a.w.notes) == 0 {
		return nil
	}
	if src, ok := a.inner.(attentionSource); !ok {
		return nil
	} else if _, ok := src.Attention(); !ok {
		return nil
	}
	notes := a.w.notes
	a.w.notes = nil
	cmds := make([]tea.Cmd, 0, len(notes))
	for _, n := range notes {
		cmds = append(cmds, func() tea.Msg { return chat.NoticeMsg{Text: n} })
	}
	return tea.Batch(cmds...)
}

func (a attentionWatch) View() string { return a.inner.View() }

// ControlSnapshot keeps the control API reading the wrapped screen.
func (a attentionWatch) ControlSnapshot() control.Snapshot {
	if sp, ok := a.inner.(control.SnapshotProvider); ok {
		return sp.ControlSnapshot()
	}
	return control.Snapshot{View: control.ViewUnknown}
}
