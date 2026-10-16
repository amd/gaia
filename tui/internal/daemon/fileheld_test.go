// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package daemon

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFileHeldSeesALockAndItsRelease(t *testing.T) {
	path := filepath.Join(t.TempDir(), "s.lock")
	if held, err := FileHeld(path); err != nil || held {
		t.Fatalf("missing file: held=%v err=%v", held, err)
	}
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if ok, err := tryLock(f); !ok || err != nil {
		t.Fatalf("test setup: could not lock: %v", err)
	}
	if held, err := FileHeld(path); err != nil || !held {
		t.Fatalf("locked file: held=%v err=%v", held, err)
	}
	unlock(f)
	if held, err := FileHeld(path); err != nil || held {
		t.Fatalf("released file: held=%v err=%v", held, err)
	}
	// The probe must not leave the file locked behind it.
	if ok, err := tryLock(f); !ok || err != nil {
		t.Fatalf("FileHeld left the lock taken: %v", err)
	}
	unlock(f)
}
