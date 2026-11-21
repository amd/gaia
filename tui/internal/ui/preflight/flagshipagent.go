// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"strings"

	"github.com/amd/gaia/tui/internal/catalog"
)

// FlagshipAgentKey is the config.json key holding which agent program answers
// as GAIA. The Agent UI reads and writes the same key.
const FlagshipAgentKey = "flagship_agent"

const (
	// FlagshipAgentPython is the Python agent, gaia-agent.
	FlagshipAgentPython = "gaiapy"
	// FlagshipAgentNative is the native C++ agent, gaiacpp.
	FlagshipAgentNative = "gaiacpp"
)

// FlagshipAgent is the saved agent program. Choice is empty when the user
// never chose one.
type FlagshipAgent struct {
	Choice string
	Path   string
}

// Program is the program name the saved choice runs, or "" with no choice.
func (f FlagshipAgent) Program() string {
	return FlagshipAgentProgram(f.Choice)
}

// FlagshipAgentProgram maps a saved choice to the program catalog selects by.
func FlagshipAgentProgram(choice string) string {
	switch choice {
	case FlagshipAgentPython:
		return catalog.PythonAgentBinary
	case FlagshipAgentNative:
		return catalog.NativeAgentBinary
	}
	return ""
}

// CheckFlagshipAgent refuses anything but the two saved values.
func CheckFlagshipAgent(choice string) error {
	if FlagshipAgentProgram(choice) == "" {
		return fmt.Errorf("%s must be %q or %q, got %q",
			FlagshipAgentKey, FlagshipAgentPython, FlagshipAgentNative, choice)
	}
	return nil
}

// ReadFlagshipAgent returns the saved agent program. A missing file or key is
// no choice; a value GAIA does not know is an error naming the file.
func ReadFlagshipAgent() (FlagshipAgent, error) {
	return readFlagshipAgent(realHostProbe())
}

func readFlagshipAgent(p hostProbe) (FlagshipAgent, error) {
	out := FlagshipAgent{Path: configPath(p)}
	if out.Path == "" {
		return out, nil
	}
	read := p.readConfig
	if read == nil {
		read = p.readFile
	}
	raw, err := read(out.Path)
	if errors.Is(err, fs.ErrNotExist) {
		return out, nil
	}
	if err != nil {
		return out, fmt.Errorf("cannot read %s: %w", out.Path, err)
	}
	var cfg struct {
		Agent *json.RawMessage `json:"flagship_agent"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return out, fmt.Errorf("the saved agent program in %s cannot be read (%v). "+
			"Fix the file by hand or delete it", out.Path, err)
	}
	if cfg.Agent == nil || string(*cfg.Agent) == "null" {
		return out, nil
	}
	var choice string
	if err := json.Unmarshal(*cfg.Agent, &choice); err != nil {
		return out, fmt.Errorf("%s: %s must be %q or %q, got %s. Fix the value or remove the key",
			out.Path, FlagshipAgentKey, FlagshipAgentPython, FlagshipAgentNative, string(*cfg.Agent))
	}
	// Python's GaiaConfig trims and lowercases this value before checking it.
	choice = strings.ToLower(strings.TrimSpace(choice))
	if err := CheckFlagshipAgent(choice); err != nil {
		return out, fmt.Errorf("%s: %w. Fix the value or remove the key", out.Path, err)
	}
	out.Choice = choice
	return out, nil
}

// WriteFlagshipAgent saves the agent program, leaving every other key alone.
func WriteFlagshipAgent(choice string) (string, error) {
	if err := CheckFlagshipAgent(choice); err != nil {
		return "", err
	}
	return writeConfigKeys(map[string]any{FlagshipAgentKey: choice})
}
