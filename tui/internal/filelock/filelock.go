// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package filelock lets the TUI edit files under ~/.gaia that Python also
// edits, without losing either side's change.
//
// It follows the contract in src/gaia/utils/file_lock.py: a writer holds an OS
// advisory lock on <name>.lock beside the target (flock on Unix, a one-byte
// LockFileEx range on Windows, which is what msvcrt.locking takes), and
// replaces the target by renaming a fully written sibling temp file over it.
//
// Python also queues waiters in <name>.lock.d/ so they are served in arrival
// order. That only affects fairness, never exclusion, which the main lock alone
// gives, so a Go writer takes the main lock directly and may overtake a
// queued Python waiter.
package filelock

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const (
	// Timeout matches LOCK_TIMEOUT_SECONDS in file_lock.py.
	Timeout = 10 * time.Second
	// renameRetry matches REPLACE_RETRY_SECONDS: Windows refuses a rename onto
	// a file another program (antivirus, indexer) has open for a moment.
	renameRetry = 3 * time.Second
	pollEvery   = 20 * time.Millisecond
)

// ErrTimeout means another process held the lock for the whole wait.
var ErrTimeout = errors.New("timed out waiting for the file lock")

// Lock is a held cross-process lock.
type Lock struct {
	f *os.File
}

// LockPath is the lock file guarding target: <name>.lock in the same folder.
func LockPath(target string) string {
	return target + ".lock"
}

// Acquire takes the lock guarding target, polling until timeout.
func Acquire(target string, timeout time.Duration) (*Lock, error) {
	path := LockPath(target)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, fmt.Errorf("cannot create %s: %w", filepath.Dir(path), err)
	}
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, fmt.Errorf("cannot open the lock file %s that guards %s: %w", path, target, err)
	}
	deadline := time.Now().Add(timeout)
	for {
		ok, lerr := TryLock(f)
		if lerr != nil {
			f.Close()
			return nil, fmt.Errorf("cannot lock %s: %w", path, lerr)
		}
		if ok {
			return &Lock{f: f}, nil
		}
		if !time.Now().Before(deadline) {
			f.Close()
			return nil, fmt.Errorf("%w: %s is held by another GAIA process; retry once it finishes", ErrTimeout, path)
		}
		time.Sleep(pollEvery)
	}
}

// Release frees the lock; a second call does nothing.
func (l *Lock) Release() {
	if l == nil || l.f == nil {
		return
	}
	Unlock(l.f)
	l.f.Close()
	l.f = nil
}

// ReadFile reads target while holding the same lock used by writers. The lock
// is intentionally exclusive: Windows may reject a read during os.Rename, and
// a short critical section keeps readers from observing a replacement in
// progress without requiring platform-specific shared-lock semantics.
func ReadFile(target string) ([]byte, error) {
	lock, err := Acquire(target, Timeout)
	if err != nil {
		return nil, err
	}
	defer lock.Release()
	return os.ReadFile(target)
}

// WriteAtomic replaces path with data. The bytes go to a temp file in the same
// folder, are fsynced, and are renamed over path, so a reader (or a crash) sees
// either the old file or the complete new one. The result has mode perm.
func WriteAtomic(path string, data []byte, perm os.FileMode) (err error) {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			os.Remove(tmp.Name())
		}
	}()
	if _, err = tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Chmod(tmp.Name(), perm); err != nil {
		return err
	}
	return renameRetrying(tmp.Name(), path)
}

func renameRetrying(src, dst string) error {
	deadline := time.Now().Add(renameRetry)
	for {
		err := os.Rename(src, dst)
		if err == nil || !renameHeld(err) || !time.Now().Before(deadline) {
			return err
		}
		time.Sleep(pollEvery)
	}
}
