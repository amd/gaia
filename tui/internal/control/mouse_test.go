// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func mouseAt(t *testing.T, action string, x, y int) []tea.MouseMsg {
	t.Helper()
	msgs, err := MouseMsgsFor(action, x, y)
	if err != nil {
		t.Fatalf("MouseMsgsFor(%q): %v", action, err)
	}
	out := make([]tea.MouseMsg, 0, len(msgs))
	for _, m := range msgs {
		mm, ok := m.(tea.MouseMsg)
		if !ok {
			t.Fatalf("got %T, want tea.MouseMsg", m)
		}
		out = append(out, mm)
	}
	return out
}

// A wheel tick is a press with no release — what a terminal actually sends.
// Injecting a release too would exercise a path no real wheel produces.
func TestWheelIsAPressWithNoRelease(t *testing.T) {
	for _, tc := range []struct {
		action string
		button tea.MouseButton
	}{
		{MouseWheelUp, tea.MouseButtonWheelUp},
		{MouseWheelDown, tea.MouseButtonWheelDown},
	} {
		got := mouseAt(t, tc.action, 5, 6)
		if len(got) != 1 {
			t.Fatalf("%s produced %d messages, want 1", tc.action, len(got))
		}
		if got[0].Action != tea.MouseActionPress || got[0].Button != tc.button {
			t.Errorf("%s = %+v", tc.action, got[0])
		}
		if got[0].X != 5 || got[0].Y != 6 {
			t.Errorf("%s landed at (%d,%d), want (5,6)", tc.action, got[0].X, got[0].Y)
		}
	}
}

func TestClickIsAPressAndARelease(t *testing.T) {
	got := mouseAt(t, MouseClick, 12, 3)
	if len(got) != 2 {
		t.Fatalf("click produced %d messages, want press and release", len(got))
	}
	if got[0].Action != tea.MouseActionPress || got[1].Action != tea.MouseActionRelease {
		t.Errorf("click = %+v", got)
	}
	if got[0].Button != tea.MouseButtonLeft {
		t.Errorf("click used button %v", got[0].Button)
	}
}

// Two presses, because that is what the app has to recognise — there is no
// double-click event on the wire for it to receive instead.
func TestDoubleClickIsTwoPresses(t *testing.T) {
	got := mouseAt(t, MouseDoubleClick, 9, 9)
	if len(got) != 4 {
		t.Fatalf("double click produced %d messages, want two press/release pairs", len(got))
	}
	presses := 0
	for _, m := range got {
		if m.Action == tea.MouseActionPress {
			presses++
		}
		if m.X != 9 || m.Y != 9 {
			t.Errorf("a message landed at (%d,%d), want (9,9)", m.X, m.Y)
		}
	}
	if presses != 2 {
		t.Errorf("%d presses, want 2", presses)
	}
}

func TestMoveIsMotionWithNoButton(t *testing.T) {
	got := mouseAt(t, MouseMove, 4, 4)
	if len(got) != 1 || got[0].Action != tea.MouseActionMotion {
		t.Fatalf("move = %+v", got)
	}
	if got[0].Button != tea.MouseButtonNone {
		t.Errorf("move pressed button %v", got[0].Button)
	}
}

func TestUnknownActionIsRefusedWithTheSupportedList(t *testing.T) {
	_, err := MouseMsgsFor("middle_click", 1, 1)
	if err == nil {
		t.Fatal("an unknown action was accepted")
	}
	for _, want := range []string{"middle_click", MouseClick, MouseWheelUp} {
		if !contains(err.Error(), want) {
			t.Errorf("the error never mentions %q: %v", want, err)
		}
	}
}

func TestNegativeCoordinatesAreRefused(t *testing.T) {
	if _, err := MouseMsgsFor(MouseClick, -1, 4); err == nil {
		t.Error("a click off the left edge was accepted")
	}
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (haystack == needle ||
		len(needle) == 0 || indexOf(haystack, needle) >= 0)
}

func indexOf(h, n string) int {
	for i := 0; i+len(n) <= len(h); i++ {
		if h[i:i+len(n)] == n {
			return i
		}
	}
	return -1
}
