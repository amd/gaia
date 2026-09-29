// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"strings"
)

// The model the user last chose, kept in ~/.gaia/config.json as
// `last_provider` + `last_model` (GaiaConfig declares both, so
// `gaia config set` keeps them). Written after every switch the agent
// confirmed, read once per launch. Restoring it is the agent's call: the TUI
// only asks, and a refusal is shown, never replaced with another model.

// LastModel is the saved choice. Provider is "local", "fireworks", "amd",
// "claude", or another Lemonade provider's name.
type LastModel struct {
	Provider string
	Model    string
	Path     string
}

// Set reports whether a choice is saved.
func (l LastModel) Set() bool { return l.Model != "" }

// ReadLastModel returns the saved choice. A missing file or key is no choice;
// a file that cannot be parsed is an error, so the launch can say why nothing
// was restored.
func ReadLastModel() (LastModel, error) {
	return readLastModel(realHostProbe())
}

func readLastModel(p hostProbe) (LastModel, error) {
	path := configPath(p)
	if path == "" {
		return LastModel{}, nil
	}
	raw, err := p.readFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return LastModel{Path: path}, nil
	}
	if err != nil {
		return LastModel{Path: path}, fmt.Errorf("cannot read %s: %w", path, err)
	}
	var cfg struct {
		Provider *string `json:"last_provider"`
		Model    *string `json:"last_model"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return LastModel{Path: path}, fmt.Errorf(
			"%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	out := LastModel{Path: path}
	if cfg.Model != nil {
		out.Model = strings.TrimSpace(*cfg.Model)
	}
	if cfg.Provider != nil {
		out.Provider = strings.TrimSpace(*cfg.Provider)
	}
	return out, nil
}

// WriteLastModel saves provider and model together.
func WriteLastModel(provider, model string) (string, error) {
	return writeConfigKeys(map[string]any{"last_provider": provider, "last_model": model})
}
