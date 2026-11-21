// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package filelock

import (
	"os"
	"path/filepath"
	"testing"
)

func TestHeldSeesALockAndItsRelease(t *testing.T) {
	path := filepath.Join(t.TempDir(), "s.lock")
	if held, err := Held(path); err != nil || held {
		t.Fatalf("missing file: held=%v err=%v", held, err)
	}
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if ok, err := TryLock(f); !ok || err != nil {
		t.Fatalf("test setup: could not lock: %v", err)
	}
	if held, err := Held(path); err != nil || !held {
		t.Fatalf("locked file: held=%v err=%v", held, err)
	}
	Unlock(f)
	if held, err := Held(path); err != nil || held {
		t.Fatalf("released file: held=%v err=%v", held, err)
	}
	// The probe must not leave the file locked behind it.
	if ok, err := TryLock(f); !ok || err != nil {
		t.Fatalf("Held left the lock taken: %v", err)
	}
	Unlock(f)
}
