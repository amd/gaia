// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/agentrecovery"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

var missingNative = &catalog.MissingProgramError{Binary: catalog.NativeAgentBinary,
	Lookup: catalog.Lookup{Looked: []string{"/h/bin"}}}

func TestOnlyASavedGaiacppIsRecovered(t *testing.T) {
	saved := AgentChoice{Program: catalog.NativeAgentBinary}
	if why, detail, ok := savedNativeBroken(saved, missingNative); !ok || !strings.Contains(why, "is not on this PC") || detail != "Looked in: /h/bin" {
		t.Errorf("a missing saved gaiacpp is not recovered: %q", why)
	}
	broken := &catalog.NativeIntegrityError{Binary: "gaiacpp", Reason: "it no longer matches what was downloaded"}
	if why, _, ok := savedNativeBroken(saved, broken); !ok || !strings.Contains(why, "no longer matches") {
		t.Errorf("a saved gaiacpp failing its check is not recovered: %q", why)
	}
	for name, tc := range map[string]struct {
		choice AgentChoice
		err    error
	}{
		"a flag is a demand":       {AgentChoice{Program: catalog.NativeAgentBinary, Pin: "--agent-program"}, missingNative},
		"the Python agent":         {AgentChoice{Program: catalog.PythonAgentBinary}, missingNative},
		"an unfinished install":    {saved, &catalog.MissingProgramError{Binary: catalog.NativeAgentBinary, Lookup: catalog.Lookup{Unverified: "/x"}}},
		"some other launch error":  {saved, errors.New("boom")},
		"a launch that worked":     {saved, nil},
		"no choice was ever saved": {AgentChoice{}, missingNative},
	} {
		if _, _, ok := savedNativeBroken(tc.choice, tc.err); ok {
			t.Errorf("%s: recovery was offered", name)
		}
	}
}

// recoveryRig replaces the screen, the saved choice and the catalog.
func recoveryRig(t *testing.T, pick agentrecovery.Choice) (saved *[]string, asked *[]string) {
	t.Helper()
	saved, asked = &[]string{}, &[]string{}
	origAsk, origSave, origCat, origTTY := askRecovery, saveFlagshipAgent, flagshipCatalog, interactive
	t.Cleanup(func() {
		askRecovery, saveFlagshipAgent, flagshipCatalog, interactive = origAsk, origSave, origCat, origTTY
	})
	interactive = func() bool { return true }
	askRecovery = func(string, string, bool, *control.Options) (agentrecovery.Choice, error) { return pick, nil }
	saveFlagshipAgent = func(choice string) (string, error) {
		*saved = append(*saved, choice)
		return "/h/config.json", nil
	}
	flagshipCatalog = func(program string) (*catalog.Catalog, string, error) {
		*asked = append(*asked, program)
		return catalog.NewCatalog(), "", nil
	}
	return saved, asked
}

func TestThePythonAgentRunsOnlyWhenPickedAndIsThenSaved(t *testing.T) {
	saved, asked := recoveryRig(t, agentrecovery.UsePython)
	_, notice, err := recoverSavedAgent("why", "detail", missingNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(*saved) != 1 || (*saved)[0] != preflight.FlagshipAgentPython || (*asked)[0] != catalog.PythonAgentBinary {
		t.Fatalf("saved %v, launched %v", *saved, *asked)
	}
	for _, want := range []string{"You chose the Python agent", "/h/config.json", "/agent gaiacpp switches back"} {
		if !strings.Contains(notice, want) {
			t.Errorf("the notice does not say %q: %s", want, notice)
		}
	}
}

// A Python agent that can't start must not replace the saved gaiacpp choice.
func TestAPythonAgentThatCannotStartIsNotSaved(t *testing.T) {
	saved, _ := recoveryRig(t, agentrecovery.UsePython)
	noPython := &catalog.MissingProgramError{Binary: catalog.PythonAgentBinary}
	flagshipCatalog = func(string) (*catalog.Catalog, string, error) { return nil, "", noPython }
	_, _, err := recoverSavedAgent("why", "detail", missingNative, false, nil)
	if !errors.Is(err, error(noPython)) || !errors.Is(err, error(missingNative)) {
		t.Fatalf("err = %v, want both the gaiacpp and the Python agent errors", err)
	}
	if len(*saved) != 0 {
		t.Fatalf("saved %v although the Python agent could not start", *saved)
	}
}

// A failed download leaves nothing to launch: no catalog, no save, the error stands.
func TestAFailedRecoveryScreenLaunchesNothing(t *testing.T) {
	saved, asked := recoveryRig(t, agentrecovery.Downloaded)
	boom := errors.New("screen failed")
	askRecovery = func(string, string, bool, *control.Options) (agentrecovery.Choice, error) {
		return agentrecovery.Downloaded, boom
	}
	if _, _, err := recoverSavedAgent("why", "detail", missingNative, false, nil); !errors.Is(err, boom) {
		t.Fatalf("err = %v", err)
	}
	if len(*saved)+len(*asked) != 0 {
		t.Fatalf("saved %v, launched %v after the screen failed", *saved, *asked)
	}
}

func TestADownloadRelaunchesOnGaiacppAndSavesNothing(t *testing.T) {
	saved, asked := recoveryRig(t, agentrecovery.Downloaded)
	if _, _, err := recoverSavedAgent("why", "detail", missingNative, false, nil); err != nil {
		t.Fatal(err)
	}
	if len(*saved) != 0 || (*asked)[0] != catalog.NativeAgentBinary {
		t.Fatalf("saved %v, launched %v", *saved, *asked)
	}
}

func TestQuittingRecoveryLaunchesNothing(t *testing.T) {
	saved, asked := recoveryRig(t, agentrecovery.Quit)
	if _, _, err := recoverSavedAgent("why", "detail", missingNative, false, nil); !errors.Is(err, errLeftRecovery) {
		t.Fatalf("err = %v", err)
	}
	if len(*saved)+len(*asked) != 0 {
		t.Fatal("quitting saved or launched something")
	}
}

// With no terminal to ask in, the launch fails with the original error.
func TestWithNoTerminalTheLaunchErrorStands(t *testing.T) {
	saved, asked := recoveryRig(t, agentrecovery.UsePython)
	interactive = func() bool { return false }
	if _, _, err := recoverSavedAgent("why", "detail", missingNative, false, nil); err != error(missingNative) {
		t.Fatalf("err = %v", err)
	}
	if len(*saved)+len(*asked) != 0 {
		t.Fatal("the Python agent was run without anyone picking it")
	}
}
