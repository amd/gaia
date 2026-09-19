// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"os"
	"testing"
)

func TestDeveloperModeIsSeparateAndInherited(t *testing.T) {
	t.Setenv("GAIA_DEVELOPER_MODE", "0")
	oldMode, oldDev := developerMode, dev
	defer func() { developerMode, dev = oldMode, oldDev }()
	developerMode, dev = false, true
	if err := applyDeveloperMode(); err != nil {
		t.Fatal(err)
	}
	if os.Getenv("GAIA_DEVELOPER_MODE") != "0" {
		t.Fatal("diagnostic --dev enabled harness engineering")
	}
	flag := rootCmd.Flags().Lookup("developer-mode")
	if flag == nil || flag.Hidden {
		t.Fatal("developer-mode flag missing")
	}
	if err := rootCmd.Flags().Set("developer-mode", "true"); err != nil {
		t.Fatal(err)
	}
	if err := applyDeveloperMode(); err != nil {
		t.Fatal(err)
	}
	if os.Getenv("GAIA_DEVELOPER_MODE") != "1" {
		t.Fatal("spawned agent will not inherit developer mode")
	}
}
