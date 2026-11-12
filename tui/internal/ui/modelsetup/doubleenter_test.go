// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	f "github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// Enter pressed again while "Saving your choices…" is in flight.
func TestASecondEnterWhileSavingStartsNoSecondRun(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.send(tea.WindowSizeMsg{Width: 100, Height: 40})
	var c1, c2 tea.Cmd
	h.m, c1 = h.m.Update(keyMsg("enter"))
	if h.m.busy == "" {
		t.Fatalf("expected busy after first enter")
	}
	h.m, c2 = h.m.Update(keyMsg("enter"))
	h.run(c1)
	// A real backend refuses a second run while one is going.
	api.RunErr = errors.New("409 a setup run is already in progress")
	h.run(c2)
	runs := 0
	for _, c := range api.CallList() {
		if strings.HasPrefix(c, "run ") {
			runs++
		}
	}
	t.Logf("calls: %v", api.CallList())
	t.Logf("dl.active=%v note=%q", h.m.dl.active, h.m.note)
	if runs > 1 {
		t.Fatalf("second enter started a second run (%d runs); dl.active=%v note=%q", runs, h.m.dl.active, h.m.note)
	}
}
