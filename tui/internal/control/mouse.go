// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"fmt"
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// Mouse input for the control API.
//
// The TUI grew real mouse behaviour — the wheel scrolls the transcript, a click
// opens a printed link, a double click copies a message, the palette and a
// question answer to hover and click — and none of it could be driven from
// here. A driver had to fall back to writing raw escape bytes at a pty, which
// tests the terminal's parser rather than the app, and cannot report what the
// app actually received.
//
// Coordinates are absolute screen cells, origin top-left, the same frame
// /screen reports: read a row off the screen, click that row.

// MouseAction names one injectable gesture.
const (
	MouseWheelUp     = "wheel_up"
	MouseWheelDown   = "wheel_down"
	MouseClick       = "click"
	MouseDoubleClick = "double_click"
	MouseRightClick  = "right_click"
	MouseMove        = "move"
)

// mouseActions maps an action name to the button and whether it is motion.
var mouseActions = map[string]struct {
	button tea.MouseButton
	motion bool
}{
	MouseWheelUp:     {tea.MouseButtonWheelUp, false},
	MouseWheelDown:   {tea.MouseButtonWheelDown, false},
	MouseClick:       {tea.MouseButtonLeft, false},
	MouseDoubleClick: {tea.MouseButtonLeft, false},
	MouseRightClick:  {tea.MouseButtonRight, false},
	MouseMove:        {tea.MouseButtonNone, true},
}

// SupportedMouseActions lists the action names, sorted, for error messages.
func SupportedMouseActions() []string {
	out := make([]string, 0, len(mouseActions))
	for name := range mouseActions {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

// MouseMsgsFor builds the messages one action produces at (x, y).
//
// A double click is two presses rather than a distinct event type, because
// that is exactly what a terminal sends and what the app has to recognise —
// injecting a synthetic "double click" the real world never produces would
// test a path no user can reach.
func MouseMsgsFor(action string, x, y int) ([]tea.Msg, error) {
	spec, ok := mouseActions[action]
	if !ok {
		return nil, fmt.Errorf("unknown mouse action %q; supported: %s",
			action, strings.Join(SupportedMouseActions(), ", "))
	}
	if x < 0 || y < 0 {
		return nil, fmt.Errorf("mouse coordinates must not be negative, got (%d, %d)", x, y)
	}

	if spec.motion {
		return []tea.Msg{tea.MouseMsg(tea.MouseEvent{
			X: x, Y: y, Action: tea.MouseActionMotion, Button: spec.button,
		})}, nil
	}

	press := tea.MouseMsg(tea.MouseEvent{
		X: x, Y: y, Action: tea.MouseActionPress, Button: spec.button,
	})
	// The wheel has no release: a terminal reports a tick as a press alone.
	if spec.button == tea.MouseButtonWheelUp || spec.button == tea.MouseButtonWheelDown {
		return []tea.Msg{press}, nil
	}

	release := tea.MouseMsg(tea.MouseEvent{
		X: x, Y: y, Action: tea.MouseActionRelease, Button: spec.button,
	})
	if action == MouseDoubleClick {
		return []tea.Msg{press, release, press, release}, nil
	}
	return []tea.Msg{press, release}, nil
}
