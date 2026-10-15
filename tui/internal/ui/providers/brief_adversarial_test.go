// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package providers

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/lemonade"
)

func loadedWith(t *testing.T, err, restoreErr error) Model {
	t.Helper()
	m := New("", 100, 40)
	next, _ := m.Update(loadedMsg{source: m.client, providers: []lemonade.Provider{{Name: "amd"}},
		err: err, restoreErr: restoreErr, started: time.Now()})
	return next.(Model)
}

func viewLines(m Model) []string { return strings.Split(ansi.Strip(m.View()), "\n") }

// Every daemon failure, however it is wrapped, is one line on the panel.
func TestEveryDaemonFailureIsOneShortLineHoweverWrapped(t *testing.T) {
	base := []error{
		&daemon.NotRunningError{Path: strings.Repeat("C:/very/long/path/", 200)},
		&daemon.StaleError{Path: "x", Reason: strings.Repeat("wedged ", 500)},
		&daemon.VersionError{Have: "1.0", Want: "9.9", Reason: strings.Repeat("r", 4000)},
		&daemon.StartError{Reason: strings.Repeat("boom\n", 300)},
		&daemon.RouteMissingError{Op: "op", Path: "/daemon/v1/x", Alternative: strings.Repeat("a", 3000) + "."},
	}
	for _, err := range base {
		for _, wrapped := range []error{
			err,
			fmt.Errorf("restore: %w", err),
			fmt.Errorf("outer: %w", fmt.Errorf("inner: %w", err)),
			keptCopyError{err},
		} {
			brief := daemon.Brief(wrapped)
			if strings.Contains(brief, "\n") || len(brief) > 200 {
				t.Errorf("%T brief is not one short line (%d bytes): %q", err, len(brief), brief)
			}
			m := loadedWith(t, nil, wrapped)
			if len(m.note) > 200 || strings.Contains(m.note, "\n") {
				t.Errorf("%T note is not one short line: %q", err, m.note)
			}
			if !m.hasDetail() {
				t.Errorf("%T: the full account is not behind d", err)
			}
			if strings.Contains(m.note, "..") {
				t.Errorf("doubled period in %q", m.note)
			}
		}
	}
}

// A plain error with no brief form, however long, must not flood the panel.
func TestAHugeUnbriefedErrorDoesNotFloodThePanel(t *testing.T) {
	huge := errors.New(strings.Repeat("connection refused by upstream proxy; ", 400) +
		"\n" + strings.Repeat("stack frame\n", 200))
	m := loadedWith(t, huge, nil)
	if n := len(viewLines(m)); n > 40 {
		t.Errorf("a %d-byte error drew %d lines on a 40-row panel", len(huge.Error()), n)
	}
	if !m.hasDetail() {
		t.Fatal("the full error is not kept behind d")
	}
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}})
	if !strings.Contains(ansi.Strip(next.(Model).View()), "stack frame") {
		t.Error("d does not reveal the full error")
	}
}

func TestDetailToggleIgnoresDWithNothingBehindIt(t *testing.T) {
	m := loadedWith(t, nil, nil)
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}})
	m = next.(Model)
	if m.showDetail {
		t.Error("d opened an empty details pane")
	}
	if strings.Contains(ansi.Strip(m.View()), "d details") {
		t.Error("the hint offers details that do not exist")
	}
}

func TestStaleDetailIsNotShownUnderANewNote(t *testing.T) {
	m := loadedWith(t, nil, &daemon.StaleError{Path: "p", Reason: "wedged"})
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}})
	m = next.(Model)
	m.note = "Key cleared, here and for future sessions."
	view := ansi.Strip(m.View())
	if strings.Contains(view, "wedged") || strings.Contains(view, "d details") {
		t.Errorf("the old error's details outlived its note:\n%s", view)
	}
}

func TestBriefOfNilIsEmpty(t *testing.T) {
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("Brief(nil) panicked: %v", r)
		}
	}()
	if got := daemon.Brief(nil); got != "" {
		t.Errorf("Brief(nil) = %q", got)
	}
}
