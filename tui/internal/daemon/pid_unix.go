//go:build !windows

package daemon

import (
	"errors"
	"syscall"
)

// PIDAlive reports whether pid refers to a running process.
//
// signal 0 probes without delivering: nil means the process exists and we may
// signal it; EPERM means it exists but is owned by another user. Either way the
// pid is taken, and the status probe is the real trust check.
func PIDAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	if err == nil {
		return true
	}
	return errors.Is(err, syscall.EPERM)
}
