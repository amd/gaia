// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
)

func TestLaunchFullAccess(t *testing.T) {
	daemon := catalog.Agent{ID: "gaia", Name: "GAIA", Transport: catalog.TransportDaemon}
	subprocess := catalog.Agent{ID: "gaia", Name: "GAIA", Transport: catalog.TransportSubprocess}

	t.Run("a saved preference never blocks a daemon launch", func(t *testing.T) {
		on, notice, err := launchFullAccess(daemon, true, true)
		if err != nil {
			t.Fatalf("a saved default refused the launch: %v", err)
		}
		if on {
			t.Error("full access must be off where the transport cannot carry it")
		}
		if !strings.Contains(notice, "Confirmation prompts are ON") || !strings.Contains(notice, "/full-access never") {
			t.Errorf("the user must be told why, and how to stop it: %q", notice)
		}
	})

	t.Run("the explicit flag is still refused on a daemon launch", func(t *testing.T) {
		_, _, err := launchFullAccess(daemon, true, false)
		if err == nil || !strings.Contains(err.Error(), "--full-access") {
			t.Fatalf("explicit --full-access over the daemon must fail naming the flag: %v", err)
		}
	})

	t.Run("a saved preference applies where it can", func(t *testing.T) {
		on, notice, err := launchFullAccess(subprocess, true, true)
		if err != nil || !on || notice != "" {
			t.Fatalf("on=%v notice=%q err=%v", on, notice, err)
		}
	})

	t.Run("off stays off with nothing to say", func(t *testing.T) {
		on, notice, err := launchFullAccess(daemon, false, false)
		if err != nil || on || notice != "" {
			t.Fatalf("on=%v notice=%q err=%v", on, notice, err)
		}
	})
}
