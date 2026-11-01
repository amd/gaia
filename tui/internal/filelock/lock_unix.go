// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package filelock

import (
	"errors"
	"os"
	"syscall"
)

// TryLock takes a non-blocking exclusive flock, the same call Python's
// gaia.daemon.lock.try_lock makes. (false, nil) means held by someone else.
func TryLock(f *os.File) (bool, error) {
	err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
		return false, nil
	}
	return false, err
}

// Unlock releases a lock taken with TryLock (closing the file also releases it).
func Unlock(f *os.File) {
	_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}
