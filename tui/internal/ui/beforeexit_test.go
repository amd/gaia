// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/control"
)

type exitRecorder struct {
	tea.Model
	called *bool
}

func (e exitRecorder) BeforeExit() error { *e.called = true; return nil }

// The final model comes back wrapped in the repaint and control layers; the
// exit work must still be found, or a closed terminal skips saving the
// conversation before a queued update installs.
func TestBeforeExitReachesTheWrappedModel(t *testing.T) {
	called := false
	inner := exitRecorder{called: &called}
	wrapped := control.NewRecorder(repaintOnResize{inner: inner}, control.NewState(control.Debugf(false)))
	beforeExit(wrapped)
	if !called {
		t.Fatal("BeforeExit was not reached through the wrappers")
	}
}
