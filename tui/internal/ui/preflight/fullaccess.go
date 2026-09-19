// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Full access as a persistent preference.
//
// `--full-access` and `/full-access confirm` are per-launch: deliberate, and
// gone when the session ends. That is right for trying it, and wrong for the
// person who works this way every day and re-types it every morning.
//
// So ~/.gaia/config.json carries `full_access`, written by
// `gaia config set full_access true` or by /full-access always. It is the ONE
// thing that can turn the mode on without somebody asking for it on this
// launch, which constrains where it may live:
//
//   - The USER's own file only. Never a project-local .env or a checked-in
//     config — a repo you cloned must not be able to switch off your
//     confirmation prompts. (This mirrors the Python side, which reads the
//     pre-dotenv environment for exactly this reason.)
//   - The banner is unaffected. It still draws on every frame the mode is on,
//     and the launch notice says WHICH source turned it on, so "why is this
//     on?" always has a visible answer.
//   - Unreadable or absent means OFF. A config that cannot be parsed must not
//     be read as permission; the safe direction is the silent one. Only a
//     JSON boolean counts: GaiaConfig.load refuses a string like "yes" for
//     this key, so the two readers of this file never disagree.

// FullAccessConfig is the persisted preference plus where it came from, so the
// UI can tell the user which file to edit without guessing the path again.
type FullAccessConfig struct {
	Enabled bool
	Path    string
}

// ReadFullAccess reports the persisted `full_access` preference.
//
// A missing file is the normal state and is not an error: it means off, the
// same answer GaiaConfig's default gives.
func ReadFullAccess() FullAccessConfig {
	return readFullAccess(realHostProbe())
}

func readFullAccess(p hostProbe) FullAccessConfig {
	path := configPath(p)
	if path == "" {
		return FullAccessConfig{}
	}
	raw, err := p.readFile(path)
	if err != nil {
		return FullAccessConfig{Path: path}
	}
	var cfg struct {
		FullAccess *bool `json:"full_access"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil || cfg.FullAccess == nil {
		// Corrupt, or the key was never written. Either way: not permission.
		return FullAccessConfig{Path: path}
	}
	return FullAccessConfig{Enabled: *cfg.FullAccess, Path: path}
}

// WriteFullAccess persists the preference, creating config.json if needed.
//
// Read-modify-write over the raw JSON object rather than a typed struct: this
// file is Python's, and every key the Go side has never heard of — profile,
// default_device, default_model, anything added later — has to survive a write
// from here untouched. Marshalling a struct would silently drop them.
func WriteFullAccess(enabled bool) (string, error) {
	path := configPath(realHostProbe())
	if path == "" {
		return "", fmt.Errorf("cannot locate ~/.gaia/config.json (no home directory)")
	}

	doc := map[string]any{}
	raw, err := os.ReadFile(path)
	if err == nil {
		if err := json.Unmarshal(raw, &doc); err != nil {
			// Refuse rather than overwrite: this file holds settings the user
			// put there, and replacing it wholesale would lose them.
			return path, fmt.Errorf(
				"%s is not valid JSON (%v) — fix or delete it, then try again", path, err)
		}
	} else if !os.IsNotExist(err) {
		return path, fmt.Errorf("cannot read %s: %w", path, err)
	}

	doc["full_access"] = enabled
	body, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return path, fmt.Errorf("cannot encode %s: %w", path, err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return path, fmt.Errorf("cannot create %s: %w", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, append(body, '\n'), 0o600); err != nil {
		return path, fmt.Errorf("cannot write %s: %w", path, err)
	}
	return path, nil
}
