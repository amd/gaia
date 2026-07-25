package daemon

import (
	"fmt"
	"os"
	"time"
)

// lockPoll is the retry interval while waiting for the start lock.
const lockPoll = 100 * time.Millisecond

// fileLock holds the exclusive daemon-start lock. Only ONE process may be in the
// "decide + spawn" critical section at a time, so two concurrent StartOrAttach
// callers yield exactly one daemon (the loser attaches to the winner's
// instance.json).
//
// An OS advisory lock is used rather than an O_EXCL create-lock because the OS
// releases it when the holder dies — a create-lock would strand a stale lock
// file after SIGKILL.
type fileLock struct {
	f *os.File
}

// acquireLock takes the exclusive lock at path, retrying until timeout. Failure
// is loud: an abandoned start attempt must surface an actionable error rather
// than hang forever.
func acquireLock(path string, timeout time.Duration) (*fileLock, error) {
	// 0600: the lock file lives beside the token-bearing instance.json.
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, &StartError{Reason: fmt.Sprintf("cannot open the start lock at %s: %v", path, err)}
	}
	deadline := time.Now().Add(timeout)
	for {
		ok, lerr := tryLock(f)
		if lerr != nil {
			f.Close()
			return nil, &StartError{Reason: fmt.Sprintf("cannot lock %s: %v", path, lerr)}
		}
		if ok {
			return &fileLock{f: f}, nil
		}
		if time.Now().After(deadline) {
			f.Close()
			return nil, &StartError{Reason: fmt.Sprintf(
				"another process held the start lock at %s for more than %s without finishing — "+
					"look for a stuck `gaia daemon start` or Agent UI launch", path, timeout)}
		}
		time.Sleep(lockPoll)
	}
}

func (l *fileLock) release() {
	if l == nil || l.f == nil {
		return
	}
	unlock(l.f)
	l.f.Close()
	l.f = nil
}
