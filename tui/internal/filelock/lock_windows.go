// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build windows

package filelock

import (
	"errors"
	"io/fs"
	"os"

	"golang.org/x/sys/windows"
)

// TryLock takes an exclusive lock on byte 0..1 of the file, the same byte range
// Python's msvcrt.locking(fd, LK_NBLCK, 1) locks at offset 0.
// (false, nil) means held by someone else.
func TryLock(f *os.File) (bool, error) {
	var ov windows.Overlapped
	err := windows.LockFileEx(
		windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY,
		0, 1, 0, &ov,
	)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, windows.ERROR_LOCK_VIOLATION) || errors.Is(err, windows.ERROR_IO_PENDING) {
		return false, nil
	}
	return false, err
}

// Unlock releases a lock taken with TryLock (closing the file also releases it).
func Unlock(f *os.File) {
	var ov windows.Overlapped
	_ = windows.UnlockFileEx(windows.Handle(f.Fd()), 0, 1, 0, &ov)
}

// renameHeld reports whether a failed rename may be another program (an
// antivirus scanner, the search indexer) briefly holding the file, which
// Python's replace_retrying waits out.
func renameHeld(err error) bool {
	return errors.Is(err, fs.ErrPermission) ||
		errors.Is(err, windows.ERROR_SHARING_VIOLATION) ||
		errors.Is(err, windows.ERROR_LOCK_VIOLATION)
}
