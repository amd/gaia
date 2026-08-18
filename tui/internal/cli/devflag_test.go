// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import "testing"

// --dev and --debug are one mode with two spellings, not two switches. If they
// ever end up bound to different variables the TUI can go verbose while the
// agent it spawns stays quiet, which is the confusing half-state this test
// exists to prevent.
func TestDevAndDebugAreTheSameSwitch(t *testing.T) {
	flags := rootCmd.PersistentFlags()

	devFlag := flags.Lookup("dev")
	if devFlag == nil {
		t.Fatal("--dev is not registered")
	}
	debugFlag := flags.Lookup("debug")
	if debugFlag == nil {
		t.Fatal("--debug was removed rather than kept as an alias; scripts and docs still pass it")
	}

	if !debugFlag.Hidden {
		t.Error("--debug is visible in --help, so one mode reads as two features")
	}
	if devFlag.Hidden {
		t.Error("--dev is hidden; it is the name of the mode and belongs in --help")
	}

	for _, tc := range []struct{ name, flag string }{
		{"the canonical name", "dev"},
		{"the deprecated alias", "debug"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dev = false
			defer func() { dev = false }()

			if err := flags.Set(tc.flag, "true"); err != nil {
				t.Fatalf("--%s: %v", tc.flag, err)
			}
			if !dev {
				t.Errorf("--%s did not turn on developer mode", tc.flag)
			}
		})
	}
}
