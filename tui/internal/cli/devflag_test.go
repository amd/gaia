// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import "testing"

// --dev and --debug are one mode with two spellings, not two switches. If they
// ever end up bound to different variables, one spelling turns the control API
// on and the other does not — the confusing half-state this test
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

// --dev is the control API and nothing else: it turns it on for an interactive
// launch (auto-assigned port, or --control-port's), and is a no-op on a
// one-shot, which has no session to drive — never a refusal.
func TestDevOnlyTurnsOnTheControlAPI(t *testing.T) {
	defer func() { dev, controlEnabled, controlPort = false, false, 0 }()
	dev, controlEnabled, controlPort = true, false, 0

	cmd := rootCmd
	opts, err := controlOptionsFor(cmd)
	if err != nil || opts == nil {
		t.Fatalf("--dev did not turn the control API on: %+v, %v", opts, err)
	}
	if opts.Port != 0 {
		t.Errorf("--dev picked port %d; it must auto-assign unless --control-port says otherwise", opts.Port)
	}

	interactive, err := controlOptionsForAgentRun(cmd, false)
	if err != nil || interactive == nil {
		t.Errorf("--dev on an interactive agent launch: %+v, %v", interactive, err)
	}
	oneShot, err := controlOptionsForAgentRun(cmd, true)
	if err != nil {
		t.Errorf("--dev with --query was refused: %v", err)
	}
	if oneShot != nil {
		t.Errorf("--dev bound a control API for a one-shot: %+v", oneShot)
	}

	dev = false
	if opts, _ := controlOptionsFor(cmd); opts != nil {
		t.Errorf("the control API is on without --dev or --control: %+v", opts)
	}
}
