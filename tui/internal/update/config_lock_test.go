// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestConcurrentWritesKeepEveryKey(t *testing.T) {
	withHome(t)
	var wg sync.WaitGroup
	errs := make(chan error, 20)
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			errs <- writeKey(fmt.Sprintf("k%d", i), i)
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	path, _ := ConfigPath()
	raw, err := readRaw(path)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 20; i++ {
		if _, ok := raw[fmt.Sprintf("k%d", i)]; !ok {
			t.Errorf("k%d was lost by a concurrent write: %v", i, raw)
		}
	}
	left, _ := filepath.Glob(filepath.Join(filepath.Dir(path), "update-config*"))
	if len(left) != 1 {
		t.Errorf("temp or lock files left behind: %v", left)
	}
}

func TestStaleLockIsTakenOver(t *testing.T) {
	withHome(t)
	path, _ := ConfigPath()
	os.MkdirAll(filepath.Dir(path), 0o755)
	lock := path + ".lock"
	os.WriteFile(lock, nil, 0o644)
	old := time.Now().Add(-time.Minute)
	os.Chtimes(lock, old, old)
	if err := SetAutoUpdate(true); err != nil {
		t.Fatalf("a crashed app's lock blocked the save: %v", err)
	}
}

func TestHeldLockFailsLoudly(t *testing.T) {
	withHome(t)
	path, _ := ConfigPath()
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path+".lock", nil, 0o644)
	err := SetAutoUpdate(true)
	if err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("want a busy error while another app holds the lock, got %v", err)
	}
}

func TestReadOnlySettingsFolderReadsAndFailsSavesPlainly(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("needs a POSIX folder that refuses new files")
	}
	withHome(t)
	path, _ := ConfigPath()
	dir := filepath.Dir(path)
	os.MkdirAll(dir, 0o755)
	os.WriteFile(path, []byte(`{"autoUpdate": true}`), 0o644)
	os.Chmod(dir, 0o555)
	t.Cleanup(func() { os.Chmod(dir, 0o755) })

	start := time.Now()
	cfg, err := ReadConfig()
	if err != nil || !cfg.AutoUpdate {
		t.Fatalf("a read-only folder must still read the settings: %+v, %v", cfg, err)
	}
	err = SetAutoUpdate(false)
	if err == nil || strings.Contains(err.Error(), "busy") || !strings.Contains(err.Error(), "permissions") {
		t.Fatalf("want a permissions error, not busy, got %v", err)
	}
	if time.Since(start) > lockWait {
		t.Errorf("waited %v for a lock that can never be taken", time.Since(start))
	}
}

func staleLock(t *testing.T) string {
	t.Helper()
	withHome(t)
	path, _ := ConfigPath()
	os.MkdirAll(filepath.Dir(path), 0o755)
	os.WriteFile(path, []byte(`{"autoUpdate": true}`), 0o644)
	lock := path + ".lock"
	os.WriteFile(lock, nil, 0o644)
	old := time.Now().Add(-time.Hour)
	os.Chtimes(lock, old, old)
	return lock
}

// within fails the test, rather than hanging the suite, if fn never returns.
func within(t *testing.T, fn func() error) error {
	t.Helper()
	done := make(chan error, 1)
	go func() { done <- fn() }()
	select {
	case err := <-done:
		return err
	case <-time.After(lockWait + 3*time.Second):
		t.Fatal("still retrying a stale lock long after the wait ran out")
		return nil
	}
}

func failRename(t *testing.T, err error, onCall func(string)) {
	t.Helper()
	prev := renameLock
	renameLock = func(from, to string) error {
		if onCall != nil {
			onCall(from)
		}
		return &os.LinkError{Op: "rename", Old: from, New: to, Err: err}
	}
	t.Cleanup(func() { renameLock = prev })
}

func TestStaleLockThatCannotBeMovedFailsWithinWait(t *testing.T) {
	for _, cause := range []error{os.ErrPermission, errors.New("input/output error")} {
		t.Run(cause.Error(), func(t *testing.T) {
			lock := staleLock(t)
			failRename(t, cause, nil)
			err := within(t, func() error { return SetAutoUpdate(false) })
			if err == nil || !strings.Contains(err.Error(), "cannot remove") || !strings.Contains(err.Error(), "permissions") {
				t.Fatalf("want an error naming the stuck lock and permissions, got %v", err)
			}
			if strings.Contains(err.Error(), "busy") {
				t.Errorf("a lock no app holds is not busy: %v", err)
			}
			if _, serr := os.Stat(lock); serr != nil {
				t.Errorf("the lock should be left for the user to remove: %v", serr)
			}
		})
	}
}

func TestStaleLockThatCannotBeMovedStillReadsOnPOSIX(t *testing.T) {
	staleLock(t)
	failRename(t, os.ErrPermission, nil)
	var cfg Config
	err := within(t, func() error {
		var rerr error
		cfg, rerr = ReadConfig()
		return rerr
	})
	if runtime.GOOS == "windows" {
		// Windows reports a lock still being deleted the same way, so it waits.
		if err == nil || !strings.Contains(err.Error(), "cannot remove") {
			t.Fatalf("want a stuck-lock error, got %v", err)
		}
		return
	}
	if err != nil || !cfg.AutoUpdate {
		t.Fatalf("a folder that refuses changes must still read the settings: %+v, %v", cfg, err)
	}
}

func TestStaleLockAlreadyMovedByAnotherAppIsNotAnError(t *testing.T) {
	staleLock(t)
	failRename(t, os.ErrNotExist, func(from string) { os.Remove(from) })
	if err := within(t, func() error { return SetAutoUpdate(false) }); err != nil {
		t.Fatalf("losing the takeover race to another app must not fail the save: %v", err)
	}
	cfg, err := ReadConfig()
	if err != nil || cfg.AutoUpdate {
		t.Fatalf("the save was lost: %+v, %v", cfg, err)
	}
}

func TestReadOnlyFolderWithStaleLock(t *testing.T) {
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("needs a POSIX folder that refuses changes")
	}
	lock := staleLock(t)
	dir := filepath.Dir(lock)
	os.Chmod(dir, 0o555)
	t.Cleanup(func() { os.Chmod(dir, 0o755) })

	var cfg Config
	err := within(t, func() error {
		var rerr error
		cfg, rerr = ReadConfig()
		return rerr
	})
	if err != nil || !cfg.AutoUpdate {
		t.Fatalf("a read-only folder must still read the settings: %+v, %v", cfg, err)
	}
	err = within(t, func() error { return SetAutoUpdate(false) })
	if err == nil || !strings.Contains(err.Error(), "cannot remove") || !strings.Contains(err.Error(), "permissions") {
		t.Fatalf("want an error naming the stuck lock and permissions, got %v", err)
	}
}
