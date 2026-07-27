package client

import (
	"bufio"
	"io"
	"strings"
)

// maxFrameBytes caps one SSE line. A tool_result payload can be large (search
// hits, an inbox pre-scan), so the ceiling is generous; beyond it the scanner
// errors instead of silently truncating the JSON into something unparseable.
const maxFrameBytes = 4 << 20

// sseFrameReader turns an SSE byte stream into JSON payloads.
//
// Framing rules (contract §3, mirroring _iter_sse_events in
// src/gaia/daemon/agent_query.py):
//   - `data:` field lines accumulate until a blank line, then join with "\n".
//   - `:`-prefixed lines are comments/heartbeats and are skipped.
//   - other SSE fields (`event:`, `id:`, `retry:`) are framing, not payload.
//   - a trailing frame with no terminating blank line is still flushed at EOF.
type sseFrameReader struct {
	sc   *bufio.Scanner
	data []string
	// onLine fires after every physical line, so a caller can reset a read-idle
	// watchdog on any traffic — including heartbeats.
	onLine func()
}

func newSSEFrameReader(r io.Reader, onLine func()) *sseFrameReader {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), maxFrameBytes)
	return &sseFrameReader{sc: sc, onLine: onLine}
}

// Next returns the next complete frame payload. ok is false once the stream ends.
func (r *sseFrameReader) Next() (payload []byte, ok bool) {
	for r.sc.Scan() {
		if r.onLine != nil {
			r.onLine()
		}
		line := r.sc.Text()
		if line == "" {
			if p, flushed := r.flush(); flushed {
				return p, true
			}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		// A field line with no colon carries an empty value, per the SSE spec
		// (and matching the reference implementation's str.partition).
		field, value, _ := strings.Cut(line, ":")
		if field != "data" {
			continue
		}
		r.data = append(r.data, strings.TrimPrefix(value, " "))
	}
	return r.flush()
}

// Err reports a read failure (including a line above maxFrameBytes). nil at a
// clean EOF.
func (r *sseFrameReader) Err() error {
	return r.sc.Err()
}

func (r *sseFrameReader) flush() ([]byte, bool) {
	if len(r.data) == 0 {
		return nil, false
	}
	payload := strings.Join(r.data, "\n")
	r.data = r.data[:0]
	return []byte(payload), true
}
