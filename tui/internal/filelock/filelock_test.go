// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package filelock

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestSecondAcquireTimesOutUntilReleased(t *testing.T) {
	target := filepath.Join(t.TempDir(), "sub", "config.json")
	first, err := Acquire(target, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(LockPath(target)) != "config.json.lock" {
		t.Fatalf("lock file must be <name>.lock, got %s", LockPath(target))
	}

	if _, err := Acquire(target, 100*time.Millisecond); !errors.Is(err, ErrTimeout) {
		t.Fatalf("want ErrTimeout while held, got %v", err)
	}

	first.Release()
	first.Release() // idempotent
	second, err := Acquire(target, time.Second)
	if err != nil {
		t.Fatalf("lock must be free after Release: %v", err)
	}
	second.Release()
}

func TestReadFileWaitsForTheWriter(t *testing.T) {
	target := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(target, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	first, err := Acquire(target, time.Second)
	if err != nil {
		t.Fatal(err)
	}

	result := make(chan struct {
		body []byte
		err  error
	}, 1)
	go func() {
		body, err := ReadFile(target)
		result <- struct {
			body []byte
			err  error
		}{body, err}
	}()

	select {
	case <-result:
		t.Fatal("ReadFile returned while the writer still held the lock")
	case <-time.After(100 * time.Millisecond):
	}
	first.Release()

	select {
	case got := <-result:
		if got.err != nil {
			t.Fatalf("ReadFile: %v", got.err)
		}
		if string(got.body) != "old" {
			t.Fatalf("ReadFile = %q, want old", got.body)
		}
	case <-time.After(time.Second):
		t.Fatal("ReadFile did not continue after the writer released the lock")
	}
}

func TestReadFileFallsBackWhenTheLockCannotBeCreated(t *testing.T) {
	const target = "C:/provisioned/config.json"
	readCalled := false
	got, err := readFile(
		target,
		func(string, time.Duration) (*Lock, error) { return nil, os.ErrPermission },
		func(path string) ([]byte, error) {
			readCalled = true
			if path != target {
				t.Fatalf("read path = %q, want %q", path, target)
			}
			return []byte(`{"full_access":true}`), nil
		},
	)
	if err != nil {
		t.Fatalf("ReadFile fallback: %v", err)
	}
	if !readCalled || string(got) != `{"full_access":true}` {
		t.Fatalf("fallback = %q, called=%v", got, readCalled)
	}
}

func TestReadFileDoesNotHideALockTimeout(t *testing.T) {
	readCalled := false
	_, err := readFile(
		"config.json",
		func(string, time.Duration) (*Lock, error) { return nil, ErrTimeout },
		func(string) ([]byte, error) {
			readCalled = true
			return nil, nil
		},
	)
	if !errors.Is(err, ErrTimeout) {
		t.Fatalf("error = %v, want ErrTimeout", err)
	}
	if readCalled {
		t.Fatal("ReadFile must not bypass a held lock")
	}
}

func TestAcquireCreatesAPrivateDirectory(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows does not expose POSIX directory permissions")
	}
	target := filepath.Join(t.TempDir(), "gaia", "config.json")
	lock, err := Acquire(target, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	info, err := os.Stat(filepath.Dir(target))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm()&0o077 != 0 {
		t.Fatalf("config directory permissions = %o, want no group/world access", info.Mode().Perm())
	}
}

func TestWriteAtomicReplacesAndCleansUp(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	for _, body := range []string{"one", "two"} {
		if err := WriteAtomic(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		got, _ := os.ReadFile(path)
		if string(got) != body {
			t.Fatalf("got %q, want %q", got, body)
		}
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Errorf("temp file left behind: %v", entries)
	}
}

func TestWriteAtomicFailureLeavesTargetAndNoTemp(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows retries a refused rename for seconds before failing")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	// A directory at the target makes the final rename fail.
	bad := filepath.Join(dir, "target")
	if err := os.Mkdir(bad, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(bad, "x"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := WriteAtomic(bad, []byte("new"), 0o600); err == nil {
		t.Fatal("expected the rename onto a directory to fail")
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if e.Name() != "config.json" && e.Name() != "target" {
			t.Errorf("temp file left after failure: %s", e.Name())
		}
	}
}
