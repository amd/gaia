// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package filelock

import (
	"errors"
	"fmt"
	"io/fs"
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

// existingTarget is config.json in a folder that exists, so readFile reaches
// the lock step.
func existingTarget(t *testing.T, body string) string {
	t.Helper()
	target := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(target, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return target
}

func failingAcquire(err error) acquireFunc {
	return func(target string, _ time.Duration) (*Lock, error) {
		return nil, fmt.Errorf("cannot open the lock file %s: %w", LockPath(target),
			&fs.PathError{Op: "open", Path: LockPath(target), Err: err})
	}
}

func probeMustNotRun(t *testing.T) probeFunc {
	return func(string) error {
		t.Fatal("the write probe must not run")
		return nil
	}
}

func TestReadFileFallsBackOnReadOnlyMedia(t *testing.T) {
	target := existingTarget(t, `{"full_access":true}`)
	got, err := readFile(target, failingAcquire(errReadOnlyFS), os.ReadFile, probeMustNotRun(t))
	if err != nil {
		t.Fatalf("read-only media must fall back to an unlocked read: %v", err)
	}
	if string(got) != `{"full_access":true}` {
		t.Fatalf("got %q", got)
	}
}

func TestReadFileFallsBackWhenTheFolderRefusesWrites(t *testing.T) {
	target := existingTarget(t, `{"full_access":true}`)
	for _, probeErr := range []error{os.ErrPermission, errReadOnlyFS} {
		got, err := readFile(target, failingAcquire(os.ErrPermission), os.ReadFile,
			func(string) error { return &fs.PathError{Op: "open", Path: "probe", Err: probeErr} })
		if err != nil {
			t.Fatalf("probe %v: want unlocked read, got %v", probeErr, err)
		}
		if string(got) != `{"full_access":true}` {
			t.Fatalf("probe %v: got %q", probeErr, got)
		}
	}
}

func TestReadFileKeepsAPermissionErrorWhenTheFolderIsWritable(t *testing.T) {
	target := existingTarget(t, "{}")
	readCalled := false
	_, err := readFile(target, failingAcquire(os.ErrPermission),
		func(string) ([]byte, error) { readCalled = true; return nil, nil },
		probeWritable)
	if !errors.Is(err, os.ErrPermission) {
		t.Fatalf("error = %v, want the lock's permission error", err)
	}
	if readCalled {
		t.Fatal("a writable folder must not bypass the lock")
	}
	entries, _ := os.ReadDir(filepath.Dir(target))
	if len(entries) != 1 {
		t.Fatalf("the probe left files behind: %v", entries)
	}
}

func TestReadFileDoesNotHideOtherLockFailures(t *testing.T) {
	target := existingTarget(t, "{}")
	_, err := readFile(target, failingAcquire(errors.New("disk on fire")),
		func(string) ([]byte, error) { t.Fatal("must not read"); return nil, nil },
		probeMustNotRun(t))
	if err == nil {
		t.Fatal("an unexpected lock failure must stay loud")
	}
}

func TestReadFileDoesNotHideALockTimeout(t *testing.T) {
	target := existingTarget(t, "{}")
	readCalled := false
	_, err := readFile(
		target,
		func(string, time.Duration) (*Lock, error) { return nil, ErrTimeout },
		func(string) ([]byte, error) {
			readCalled = true
			return nil, nil
		},
		func(string) error { return os.ErrPermission },
	)
	if !errors.Is(err, ErrTimeout) {
		t.Fatalf("error = %v, want ErrTimeout", err)
	}
	if readCalled {
		t.Fatal("ReadFile must not bypass a held lock")
	}
}

func TestReadFileLeavesAMissingFolderAlone(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, ".gaia")
	_, err := ReadFile(filepath.Join(dir, "config.json"))
	if !os.IsNotExist(err) {
		t.Fatalf("error = %v, want not-exist", err)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("a read must not create %s (stat err %v)", dir, err)
	}
	if entries, _ := os.ReadDir(home); len(entries) != 0 {
		t.Fatalf("a read left files behind: %v", entries)
	}
}

func TestReadFileTreatsAFileInPlaceOfTheFolderAsMissing(t *testing.T) {
	home := t.TempDir()
	notADir := filepath.Join(home, ".gaia")
	if err := os.WriteFile(notADir, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := ReadFile(filepath.Join(notADir, "config.json"))
	if !os.IsNotExist(err) || !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("error = %v, want not-exist", err)
	}
	if body, _ := os.ReadFile(notADir); string(body) != "x" {
		t.Fatalf("the file at %s was changed: %q", notADir, body)
	}
	if entries, _ := os.ReadDir(home); len(entries) != 1 {
		t.Fatalf("a read left files behind: %v", entries)
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
