package client

import (
	"bytes"
	"sync"
	"unicode/utf8"
)

// agentStderrCap is how much of a child's stderr is kept. The text is only
// shown when the child dies, and its last lines are what explain the exit.
const agentStderrCap = 64 * 1024

// stderrDroppedMarker heads a tail whose earlier output was discarded, so a
// reader knows the first line shown is not where the process started.
const stderrDroppedMarker = "[earlier output dropped]\n"

// tailBuffer is an io.Writer that keeps only the last limit bytes written.
// A long-lived agent writes stderr for its whole life; an unbounded buffer
// grows with every stray print and is read at most once, on exit.
type tailBuffer struct {
	mu      sync.Mutex
	limit   int
	buf     []byte
	dropped bool
}

func newTailBuffer(limit int) *tailBuffer {
	return &tailBuffer{limit: limit}
}

// Write never fails: dropping old output is the point, not an error.
func (t *tailBuffer) Write(p []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	n := len(p)
	if n > t.limit {
		t.buf = append(t.buf[:0], p[n-t.limit:]...)
		t.dropped = true
		return n, nil
	}
	t.buf = append(t.buf, p...)
	// Trimmed only past twice the limit, so the copy is amortised over many writes.
	if len(t.buf) > 2*t.limit {
		kept := copy(t.buf, t.buf[len(t.buf)-t.limit:])
		t.buf = t.buf[:kept]
		t.dropped = true
	}
	return n, nil
}

// String returns at most the last limit bytes, starting on a whole line when
// earlier output was dropped.
func (t *tailBuffer) String() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	tail := t.buf
	dropped := t.dropped
	if len(tail) > t.limit {
		tail = tail[len(tail)-t.limit:]
		dropped = true
	}
	if !dropped {
		return string(tail)
	}
	if i := bytes.IndexByte(tail, '\n'); i >= 0 && i+1 < len(tail) {
		tail = tail[i+1:]
	} else {
		for len(tail) > 0 && !utf8.RuneStart(tail[0]) {
			tail = tail[1:]
		}
	}
	return stderrDroppedMarker + string(tail)
}
