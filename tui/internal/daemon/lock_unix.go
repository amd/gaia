//go:build !windows

package daemon

import (
	"errors"
	"os"
	"syscall"
)

// tryLock takes a non-blocking exclusive flock. (false, nil) means "held by
// someone else, retry"; a non-nil error is a real failure.
func tryLock(f *os.File) (bool, error) {
	err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if err == nil {
		return true, nil
	}
	if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
		return false, nil
	}
	return false, err
}

func unlock(f *os.File) {
	_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}
