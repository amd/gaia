// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"errors"
	"fmt"
	"os"
	"strings"

	xterm "golang.org/x/term"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/agentrecovery"
	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// errLeftRecovery is the user quitting the recovery screen: not a failure.
var errLeftRecovery = errors.New("left the agent recovery screen")

// Seams for tests: the screen, the saved choice and the catalog.
var (
	askRecovery = func(why, detail string, dev bool, ctrl *control.Options) (agentrecovery.Choice, error) {
		var picked agentrecovery.Choice
		err := run(agentrecovery.New(why, detail, &picked), dev, ctrl)
		return picked, err
	}
	saveFlagshipAgent = preflight.WriteFlagshipAgent
	flagshipCatalog   = catalog.ForFlagship
	interactive       = func() bool {
		return xterm.IsTerminal(int(os.Stdin.Fd())) && xterm.IsTerminal(int(os.Stdout.Fd()))
	}
)

// savedNativeBroken reports whether loadErr is the saved gaiacpp failing to
// run in a way a download repairs. why is one sentence saying which; detail is
// what the screen shows on request, without the error's advice for a flag.
func savedNativeBroken(choice AgentChoice, loadErr error) (why, detail string, ok bool) {
	// A flag or a variable is a demand for this launch; only a saved choice is recovered.
	if choice.Pin != "" || choice.Program != catalog.NativeAgentBinary {
		return "", "", false
	}
	var missing *catalog.MissingProgramError
	if errors.As(loadErr, &missing) && missing.Binary == catalog.NativeAgentBinary && missing.Lookup.Unverified == "" {
		return catalog.NativeAgentBinary + ", the agent you chose, is not on this PC.",
			"Looked in: " + strings.Join(missing.Lookup.Looked, ", "), true
	}
	var broken *catalog.NativeIntegrityError
	if errors.As(loadErr, &broken) {
		return fmt.Sprintf("%s, the agent you chose, was not run: %s.", catalog.NativeAgentBinary, broken.Reason),
			fmt.Sprintf("File: %s\nDownload record: %s", broken.Binary, broken.Record), true
	}
	return "", "", false
}

// recoverSavedAgent asks what to do about a saved gaiacpp that cannot run and
// returns the catalog for the pick. The Python agent runs only if it is picked.
func recoverSavedAgent(why, detail string, loadErr error, dev bool, ctrl *control.Options) (*catalog.Catalog, string, error) {
	if ctrl == nil && !interactive() {
		return nil, "", loadErr
	}
	picked, err := askRecovery(why, detail, dev, ctrl)
	if err != nil {
		return nil, "", errors.Join(loadErr, err)
	}
	switch picked {
	case agentrecovery.Downloaded:
		return flagshipCatalog(catalog.NativeAgentBinary)
	case agentrecovery.UsePython:
		// Found before anything is saved, as /agent does: a Python agent that can't start keeps gaiacpp saved.
		cat, notice, err := flagshipCatalog(catalog.PythonAgentBinary)
		if err != nil {
			return nil, "", errors.Join(loadErr, err)
		}
		path, err := saveFlagshipAgent(preflight.FlagshipAgentPython)
		if err != nil {
			return nil, "", fmt.Errorf("could not save the Python agent as your agent program: %w", err)
		}
		return cat, joinNotices(fmt.Sprintf(
			"You chose the Python agent because %s could not start. It is saved as your agent program (%s); /agent %s switches back.",
			catalog.NativeAgentBinary, path, preflight.FlagshipAgentNative), notice), nil
	}
	return nil, "", errLeftRecovery
}
