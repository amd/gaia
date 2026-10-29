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
	"runtime"
	"time"
)

// The preferences live in ~/.gaia/update-config.json, the file the Agent UI's
// updater (services/auto-updater.cjs) already keeps. `autoUpdate` is shared
// with it, so one switch governs both apps. The pin is the Terminal UI's own
// (`tuiPinnedVersion`): the desktop app's `pinnedVersion` also re-points its
// download feed, which a terminal pin must not do.
const (
	autoUpdateKey = "autoUpdate"
	prereleaseKey = "includePrereleases"
	pinKey        = "tuiPinnedVersion"
)

// homeDir is swapped by tests.
var homeDir = os.UserHomeDir

// Config is the saved update preferences.
type Config struct {
	AutoUpdate    bool   // install a newer release on quit; off by default
	Prereleases   bool   // offer release candidates too; off by default
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
	var raw map[string]any
	err = withConfigLock(path, true, func() error {
		raw, err = readRaw(path)
		return err
	})
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

// SetPrereleases saves whether release candidates are offered as updates.
// It is a channel, not an install trigger: with auto-update off an offered
// candidate still waits for /update now.
func SetPrereleases(on bool) error { return writeKey(prereleaseKey, on) }

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
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("cannot create %s: %w", filepath.Dir(path), err)
	}
	return withConfigLock(path, false, func() error {
		raw, err := readRaw(path)
		if err != nil {
			return err
		}
		raw[key] = value
		data, err := json.MarshalIndent(raw, "", "  ")
		if err != nil {
			return fmt.Errorf("cannot encode update settings: %w", err)
		}
		tmp, err := os.CreateTemp(filepath.Dir(path), "update-config-*.tmp")
		if err != nil {
			return fmt.Errorf("cannot write update settings next to %s: %w", path, err)
		}
		defer os.Remove(tmp.Name())
		_, werr := tmp.Write(data)
		if cerr := tmp.Close(); werr == nil {
			werr = cerr
		}
		if werr != nil {
			return fmt.Errorf("cannot write %s: %w", tmp.Name(), werr)
		}
		if err := os.Rename(tmp.Name(), path); err != nil {
			return fmt.Errorf("cannot save %s: %w", path, err)
		}
		return nil
	})
}

// The Agent UI's updater honours the same lock file, so neither app's
// read-modify-write drops the other's change.
const (
	lockWait  = 3 * time.Second
	lockStale = 10 * time.Second
)

// renameLock is swapped in tests to simulate a lock that cannot be moved aside.
var renameLock = os.Rename

func withConfigLock(path string, readOnly bool, fn func() error) error {
	lock := path + ".lock"
	deadline := time.Now().Add(lockWait)
	var lastErr error
	for {
		f, err := os.OpenFile(lock, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
		if err == nil {
			f.Close()
			break
		}
		if errors.Is(err, fs.ErrNotExist) {
			return fn() // no ~/.gaia yet: nothing to race over
		}
		// Windows reports a lock still being deleted as access denied.
		if !errors.Is(err, fs.ErrExist) && !errors.Is(err, fs.ErrPermission) {
			return fmt.Errorf("cannot lock %s: %w", path, err)
		}
		lastErr = err
		info, serr := os.Stat(lock)
		if errors.Is(err, fs.ErrPermission) && errors.Is(serr, fs.ErrNotExist) && runtime.GOOS != "windows" {
			// The folder refuses new files, so no writer can replace the file
			// under a reader either (rename is atomic here).
			if readOnly {
				return fn()
			}
			return fmt.Errorf("cannot save update settings: %s does not allow new files (%v) — fix its permissions", filepath.Dir(path), err)
		}
		var staleErr error
		if serr == nil && time.Since(info.ModTime()) > lockStale {
			// Left by a crashed app. Two takers racing on the same stale lock
			// can both get in; rare enough to accept for a settings file.
			aside := fmt.Sprintf("%s.%d.stale", lock, os.Getpid())
			rerr := renameLock(lock, aside)
			if rerr == nil || errors.Is(rerr, fs.ErrNotExist) {
				os.Remove(aside)
				continue
			}
			if readOnly && errors.Is(rerr, fs.ErrPermission) && runtime.GOOS != "windows" {
				return fn() // the folder refuses changes, so no writer can race the read
			}
			staleErr = rerr
		}
		if time.Now().After(deadline) {
			if staleErr != nil {
				return fmt.Errorf("cannot remove %s, left by a GAIA app that stopped (%v) — fix the permissions of %s or delete the lock file by hand", lock, staleErr, filepath.Dir(path))
			}
			if serr == nil {
				return fmt.Errorf("%s is busy (another GAIA app holds %s) — try again in a moment", path, lock)
			}
			return fmt.Errorf("cannot lock %s: %w — check the permissions of %s", path, lastErr, filepath.Dir(path))
		}
		time.Sleep(25 * time.Millisecond)
	}
	defer os.Remove(lock)
	return fn()
}
