// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/phone"
	"github.com/amd/gaia/tui/internal/phone/phonetest"
)

func usePhoneFake(t *testing.T) *phonetest.Fake {
	t.Helper()
	f := phonetest.New(t)
	prev := newPhoneAPI
	newPhoneAPI = func(bool) *phone.Client { return f.Client() }
	t.Cleanup(func() { newPhoneAPI = prev })
	return f
}

// pollOnce runs one banner poll: the tick, then its fetch.
func pollOnce(t *testing.T, m ChatModel) (ChatModel, tea.Cmd) {
	t.Helper()
	next, cmd := m.Update(phoneBannerTickMsg{})
	m = next.(ChatModel)
	if cmd == nil {
		return m, nil
	}
	next, cmd = m.Update(cmd())
	return next.(ChatModel), cmd
}

func TestPhoneBannerAppearsAndClearsWhenHandled(t *testing.T) {
	f := usePhoneFake(t)
	m, _ := newTestModel(t)
	m.resize()
	rowsBefore := m.viewport.Height

	m, next := pollOnce(t, m)
	if strings.Contains(ansi.Strip(m.View()), "⚠") || next == nil {
		t.Fatal("a banner with no open alert, or the poll stopped")
	}

	id := f.AddAlert("Scheduled job failed")
	m, _ = pollOnce(t, m)
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "⚠ Scheduled job failed · /phone") {
		t.Fatalf("no banner:\n%s", view)
	}
	if m.viewport.Height != rowsBefore-1 {
		t.Fatalf("the banner row wasn't taken from the transcript: %d → %d", rowsBefore, m.viewport.Height)
	}
	if got := len(strings.Split(m.View(), "\n")); got != m.height {
		t.Fatalf("frame is %d rows, window %d", got, m.height)
	}

	f.Mu.Lock()
	for i := range f.Alerts {
		if f.Alerts[i].ID == id {
			f.Alerts[i].State = "handled"
		}
	}
	f.Mu.Unlock()
	m, _ = pollOnce(t, m)
	if strings.Contains(ansi.Strip(m.View()), "⚠") || m.viewport.Height != rowsBefore {
		t.Fatal("the banner stayed after the alert was handled")
	}
}

func TestPhoneBannerStopsQuietlyOnAnOlderDaemon(t *testing.T) {
	f := usePhoneFake(t)
	f.Missing = true
	m, _ := newTestModel(t)
	msgs := len(m.messages)
	m, next := pollOnce(t, m)
	if next != nil || !m.phoneBannerOff {
		t.Fatal("the poll kept going against a daemon with no phone routes")
	}
	if len(m.messages) != msgs || strings.Contains(ansi.Strip(m.View()), "⚠") {
		t.Fatal("a missing phone feature put something on screen")
	}
	calls := len(f.Requests())
	m, _ = pollOnce(t, m)
	if len(f.Requests()) != calls {
		t.Fatal("a stopped poll still asked the daemon")
	}
}

func TestPhoneCommandOpensThePanelAndEscReturns(t *testing.T) {
	f := usePhoneFake(t)
	f.AddAlert("Scheduled job failed")
	m, _ := newTestModel(t)
	next, cmd := m.submit("/phone")
	m = next.(ChatModel)
	if m.phonePanel == nil {
		t.Fatal("/phone opened nothing")
	}
	next, _ = m.Update(cmd())
	m = next.(ChatModel)
	if view := ansi.Strip(m.View()); !strings.Contains(view, "Phone alerts") || !strings.Contains(view, "Alerts (1)") {
		t.Fatalf("panel view:\n%s", view)
	}
	next, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = next.(ChatModel)
	next, _ = m.Update(cmd())
	m = next.(ChatModel)
	if m.phonePanel != nil {
		t.Fatal("Esc left the panel open")
	}
}
