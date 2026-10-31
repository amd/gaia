// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const configLockTimeout = 10 * time.Second

// withConfigFileLock serializes TUI read-modify-writes with Python's
// gaia.utils.file_lock. Both sides lock the sibling <config>.lock file using
// the platform's advisory locking primitive, so a TUI write cannot race a
// `gaia config` or Agent UI update.
func withConfigFileLock(configPath string, fn func() error) error {
	lockPath := configPath + ".lock"
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o755); err != nil {
		return fmt.Errorf("cannot create %s: %w", filepath.Dir(lockPath), err)
	}
	lockFile, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return fmt.Errorf("cannot open config lock %s: %w", lockPath, err)
	}
	defer lockFile.Close()

	deadline := time.Now().Add(configLockTimeout)
	for {
		locked, err := tryLockConfigFile(lockFile)
		if err != nil {
			return fmt.Errorf("cannot lock %s: %w", lockPath, err)
		}
		if locked {
			defer unlockConfigFile(lockFile)
			return fn()
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("timed out after %s waiting for %s", configLockTimeout, lockPath)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// writeConfigFileAtomic replaces configPath without exposing a truncated JSON
// file. The temporary file is created beside the target so replacement stays
// on one filesystem; platform files provide the appropriate replace primitive.
func writeConfigFileAtomic(configPath string, data []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(configPath), ".config.json-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	removeTemp := true
	defer func() {
		_ = tmp.Close()
		if removeTemp {
			_ = os.Remove(tmpPath)
		}
	}()
	if err := tmp.Chmod(0o600); err != nil {
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		return err
	}
	if err := tmp.Sync(); err != nil {
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := replaceConfigFile(tmpPath, configPath); err != nil {
		return err
	}
	removeTemp = false
	return nil
}
