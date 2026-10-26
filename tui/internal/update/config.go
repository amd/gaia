// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package update finds newer GAIA releases and installs the one the user
// picks. Nothing installs unasked: auto-update is off by default, and when it
// is on the install still waits until the user quits.
package update

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// The preferences live in ~/.gaia/update-config.json, the file the Agent UI's
// updater (services/auto-updater.cjs) already keeps. `autoUpdate` is shared
// with it, so one switch governs both apps. The pin is the Terminal UI's own
// (`tuiPinnedVersion`): the desktop app's `pinnedVersion` also re-points its
// download feed, which a terminal pin must not do.
const (
	autoUpdateKey = "autoUpdate"
	prereleaseKey = "autoUpdatePrereleases"
	pinKey        = "tuiPinnedVersion"
)

// homeDir is swapped by tests.
var homeDir = os.UserHomeDir

// Config is the saved update preferences.
type Config struct {
	AutoUpdate    bool   // install a newer release on quit; off by default
	Prereleases   bool   // auto-update takes release candidates too; needs AutoUpdate
	PinnedVersion string // a tag such as "v0.25.0", or "" when not pinned
	Path          string
}

// ConfigPath is where the preferences are kept.
func ConfigPath() (string, error) {
	home, err := homeDir()
	if err != nil {
		return "", fmt.Errorf("cannot find your home folder to read update settings: %w", err)
	}
	return filepath.Join(home, ".gaia", "update-config.json"), nil
}

// ReadConfig returns the saved preferences. A missing file is the defaults; a
// file that cannot be parsed is an error, never silently the defaults.
func ReadConfig() (Config, error) {
	path, err := ConfigPath()
	if err != nil {
		return Config{}, err
	}
	raw, err := readRaw(path)
	if err != nil {
		return Config{Path: path}, err
	}
	cfg := Config{Path: path}
	if v, ok := raw[autoUpdateKey].(bool); ok {
		cfg.AutoUpdate = v
	}
	if v, ok := raw[prereleaseKey].(bool); ok {
		cfg.Prereleases = v
	}
	if v, ok := raw[pinKey].(string); ok {
		cfg.PinnedVersion = v
	}
	return cfg, nil
}

// SetAutoUpdate saves the auto-update preference.
func SetAutoUpdate(on bool) error { return writeKey(autoUpdateKey, on) }

// IncludeRCs reports whether release candidates count as updates: only while
// auto-update is on, so a saved "on" does nothing once auto-update is off.
func (c Config) IncludeRCs() bool { return c.AutoUpdate && c.Prereleases }

// SetPrereleases saves whether auto-update takes release candidates. Turning
// it on is refused while auto-update is off.
func SetPrereleases(on bool) error {
	if on {
		cfg, err := ReadConfig()
		if err != nil {
			return err
		}
		if !cfg.AutoUpdate {
			return errors.New("turn on auto-update first (/update auto on) — release candidates are an option of auto-update")
		}
	}
	return writeKey(prereleaseKey, on)
}

// SetPin pins the Terminal UI to tag; "" unpins.
func SetPin(tag string) error {
	if tag == "" {
		return writeKey(pinKey, nil)
	}
	return writeKey(pinKey, tag)
}

func readRaw(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("cannot read %s: %w", path, err)
	}
	raw := map[string]any{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	return raw, nil
}

// writeKey merges one key into the file, keeping every other key (the Agent
// UI's feedUrl and pin among them).
func writeKey(key string, value any) error {
	path, err := ConfigPath()
	if err != nil {
		return err
	}
	raw, err := readRaw(path)
	if err != nil {
		return err
	}
	raw[key] = value
	data, err := json.MarshalIndent(raw, "", "  ")
	if err != nil {
		return fmt.Errorf("cannot encode update settings: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("cannot create %s: %w", filepath.Dir(path), err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return fmt.Errorf("cannot write %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("cannot save %s: %w", path, err)
	}
	return nil
}
