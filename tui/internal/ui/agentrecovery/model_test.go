// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package agentrecovery

import (
	"context"
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiaprogram"
)

const why = "gaiacpp, the agent you chose, is not on this PC."

var available = gaiaprogram.Status{CanDownload: true, SizeBytes: 12 << 20, Version: "0.27.0"}

func screen(t *testing.T, st gaiaprogram.Status, download func(context.Context, func(gaiaprogram.Progress)) (string, error)) (Model, *Choice) {
	t.Helper()
	picked := UsePython // New must reset it: closing the screen is Quit.
	m := New(why, "Looked in: /h/bin", &picked).WithProgram(
		func(context.Context) (gaiaprogram.Status, error) { return st, nil }, download)
	if picked != Quit {
		t.Fatal("a screen nobody answered must mean Quit")
	}
	updated, _ := m.Update(m.Init()())
	return updated.(Model), &picked
}

func key(t *testing.T, m Model, k string) (Model, tea.Cmd) {
	t.Helper()
	msg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
	switch k {
	case "enter":
		msg = tea.KeyMsg{Type: tea.KeyEnter}
	case "esc":
		msg = tea.KeyMsg{Type: tea.KeyEsc}
	case "down":
		msg = tea.KeyMsg{Type: tea.KeyDown}
	}
	updated, cmd := m.Update(msg)
	return updated.(Model), cmd
}

func TestTheScreenSaysWhyAndOffersBothWaysOut(t *testing.T) {
	m, _ := screen(t, available, nil)
	view := m.View()
	for _, want := range []string{"GAIA's agent didn't start", why, "Nothing has been switched",
		"Download gaiacpp again", "12.0 MB", "Use the Python agent", "Quit"} {
		if !strings.Contains(view, want) {
			t.Errorf("the screen does not say %q:\n%s", want, view)
		}
	}
	if strings.Contains(view, "Looked in: /h/bin") {
		t.Error("the raw detail is shown before it is asked for")
	}
	m, _ = key(t, m, "?")
	if !strings.Contains(m.View(), "Looked in: /h/bin") {
		t.Error("? does not show the detail")
	}
}

func TestPickingThePythonAgentIsTheUsersChoice(t *testing.T) {
	m, picked := screen(t, available, nil)
	m, _ = key(t, m, "down")
	if *picked != Quit {
		t.Fatal("moving the cursor chose something")
	}
	_, cmd := key(t, m, "enter")
	if *picked != UsePython || cmd == nil {
		t.Fatalf("picked = %v", *picked)
	}
}

func TestDownloadShowsProgressAndEndsOnGaiacpp(t *testing.T) {
	release := make(chan struct{})
	m, picked := screen(t, available, func(_ context.Context, progress func(gaiaprogram.Progress)) (string, error) {
		progress(gaiaprogram.Progress{Done: 3 << 20, Total: 12 << 20})
		<-release
		return "/h/bin/gaiacpp", nil
	})
	m, cmd := key(t, m, "enter")
	if cmd == nil || m.ControlSnapshot().Overlay != "downloading" {
		t.Fatal("Enter on the download row did not start it")
	}
	updated, cmd := m.Update(cmd())
	m = updated.(Model)
	if view := m.View(); !strings.Contains(view, "3.0 MB of 12.0 MB (25%)") {
		t.Fatalf("no progress:\n%s", view)
	}
	close(release)
	_, quit := m.Update(cmd())
	if *picked != Downloaded || quit == nil {
		t.Fatalf("picked = %v", *picked)
	}
}

func TestAFailedDownloadStaysOnTheScreenWithTheReason(t *testing.T) {
	m, picked := screen(t, available, func(context.Context, func(gaiaprogram.Progress)) (string, error) {
		return "", errors.New("SHA-256 mismatch")
	})
	m, cmd := key(t, m, "enter")
	updated, quit := m.Update(cmd())
	m = updated.(Model)
	if quit != nil || *picked != Quit {
		t.Fatal("a failed download left the screen")
	}
	if view := m.View(); !strings.Contains(view, "gaiacpp was not installed: SHA-256 mismatch") || !strings.Contains(view, "Use the Python agent") {
		t.Fatalf("the failure or the way out is missing:\n%s", view)
	}
}

// A release with no gaiacpp: the row says so and cannot be picked.
func TestAReleaseWithoutGaiacppDisablesTheDownload(t *testing.T) {
	m, picked := screen(t, gaiaprogram.Status{Reason: "this GAIA release has no gaiacpp to download."}, nil)
	if view := m.View(); !strings.Contains(view, "Not available: this GAIA release has no gaiacpp to download.") {
		t.Fatalf("the reason is not shown:\n%s", view)
	}
	if m.cursor != 1 {
		t.Fatalf("the cursor rests on a row that cannot be picked: %d", m.cursor)
	}
	m.cursor = 0
	_, cmd := key(t, m, "enter")
	if cmd != nil || *picked != Quit {
		t.Fatal("a disabled download was started")
	}
}

func TestQuitChoosesNothing(t *testing.T) {
	m, picked := screen(t, available, nil)
	if _, cmd := key(t, m, "q"); cmd == nil || *picked != Quit {
		t.Fatalf("q: picked = %v", *picked)
	}
}
