package event

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// TraceWriter appends every canonical event the TUI receives to a JSONL file,
// verbatim, in arrival order.
//
// It exists because the TUI is the only process that sees the whole trajectory:
// tool calls WITH their arguments and tool results WITH their payloads. The
// agent-side recorder (src/gaia/agents/base/turn_metrics.py) records neither, so
// a tool that returns `status: "success"` and zero results reads as a healthy
// turn there and as a broken one here (#3576).
//
// A nil *TraceWriter is the "tracing off" state and every method accepts it, so
// call sites tap the stream unconditionally.
type TraceWriter struct {
	path string

	mu  sync.Mutex
	f   *os.File
	buf *bufio.Writer
	seq int
	// err is the FIRST write failure. Sticky, and returned by Close: a trace
	// that stopped recording halfway must not be mistaken for a complete one.
	err error
	// fresh records that this open found no existing content, so Close may
	// remove a file it created and never wrote to. A launch can fail after the
	// trace opens — a refused port, an unmet precondition — and ~/.gaia/traces
	// has no rotation, so empty files would just pile up there forever.
	fresh bool
	// now is injected by tests. Production leaves it nil and uses time.Now.
	now func() time.Time
}

// traceRecord is one line of the trace.
//
// At and Seq are the point of the wrapper: ordering and wall-clock time are what
// make a trajectory analysable, and the SSE payload carries neither. Exactly one
// of Event / Unparsed / UnparsedB64 is set — the latter two keep a payload the
// parser could not read, which is precisely the case a trace is most needed for.
type traceRecord struct {
	At       string          `json:"at"`
	Seq      int             `json:"seq"`
	Event    json.RawMessage `json:"event,omitempty"`
	Unparsed string          `json:"unparsed,omitempty"`
	// UnparsedB64 carries a payload that is not valid UTF-8. Encoding a Go
	// string to JSON replaces every bad byte with U+FFFD, so a subprocess
	// emitting binary garbage would be recorded as mangled text — losing the
	// bytes that explain what went wrong.
	UnparsedB64 string `json:"unparsed_b64,omitempty"`
}

// NewTraceWriter opens path for appending, creating its parent directory.
//
// It fails rather than degrades: a trace nobody can write is the exact silent
// non-recording this whole feature exists to remove, so the caller must treat
// this error as fatal to the launch.
func NewTraceWriter(path string) (*TraceWriter, error) {
	if strings.TrimSpace(path) == "" {
		return nil, fmt.Errorf("no trace path was given, so there is nowhere to record")
	}
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return nil, fmt.Errorf("cannot create the trace directory %s: %w "+
				"(pass --trace=<path> to record somewhere writable)", dir, err)
		}
	}
	// Whether this open CREATED the file, so Close can clean up after a launch
	// that recorded nothing without ever deleting an earlier run's trace.
	fresh := false
	if info, statErr := os.Stat(path); os.IsNotExist(statErr) {
		fresh = true
	} else if statErr == nil && info.Size() == 0 {
		fresh = true
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600) // #nosec G304 -- the path is the user's own --trace argument
	if err != nil {
		return nil, fmt.Errorf("cannot open the trace file %s: %w "+
			"(pass --trace=<path> to record somewhere writable)", path, err)
	}
	return &TraceWriter{path: path, f: f, buf: bufio.NewWriter(f), fresh: fresh}, nil
}

// Path is where the trace is being written. "" when tracing is off.
func (t *TraceWriter) Path() string {
	if t == nil {
		return ""
	}
	return t.path
}

// Write records one raw event payload as a single JSON line.
//
// Flushed per event: a run that crashes or is killed mid-turn must still leave
// behind everything it had received, which is the run a trace is most wanted for.
func (t *TraceWriter) Write(raw []byte) error {
	if t == nil {
		return nil
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.f == nil {
		return t.err
	}

	t.seq++
	rec := traceRecord{At: t.timestamp(), Seq: t.seq}
	// Compact both validates and normalises: a payload that arrived pretty-printed
	// still has to occupy exactly one line.
	var compact bytes.Buffer
	switch {
	case json.Compact(&compact, raw) == nil:
		rec.Event = compact.Bytes()
	case utf8.Valid(raw):
		rec.Unparsed = string(raw)
	default:
		rec.UnparsedB64 = base64.StdEncoding.EncodeToString(raw)
	}

	line, err := json.Marshal(rec)
	if err != nil {
		return t.failLocked(fmt.Errorf("cannot encode trace record %d: %w", t.seq, err))
	}
	if _, err := t.buf.Write(append(line, '\n')); err != nil {
		return t.failLocked(fmt.Errorf("cannot write to the trace file %s: %w", t.path, err))
	}
	if err := t.buf.Flush(); err != nil {
		return t.failLocked(fmt.Errorf("cannot flush the trace file %s: %w", t.path, err))
	}
	return nil
}

// Close flushes and closes the file, returning the first write failure if there
// was one. Safe to call twice.
func (t *TraceWriter) Close() error {
	if t == nil {
		return nil
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.f == nil {
		return t.err
	}
	f := t.f
	t.f = nil
	if err := t.buf.Flush(); err != nil && t.err == nil {
		t.err = fmt.Errorf("cannot flush the trace file %s: %w", t.path, err)
	}
	if err := f.Close(); err != nil && t.err == nil {
		t.err = fmt.Errorf("cannot close the trace file %s: %w", t.path, err)
	}
	// Nothing was ever recorded and this open created the file: the launch died
	// before the agent said anything. Leave no 0-byte trace behind.
	if t.seq == 0 && t.fresh && t.err == nil {
		if err := os.Remove(t.path); err != nil && !os.IsNotExist(err) {
			t.err = fmt.Errorf("cannot remove the empty trace file %s: %w", t.path, err)
		}
	}
	return t.err
}

// failLocked records the first failure and returns it. Caller holds t.mu.
func (t *TraceWriter) failLocked(err error) error {
	if t.err == nil {
		t.err = err
	}
	return err
}

func (t *TraceWriter) timestamp() string {
	clock := time.Now
	if t.now != nil {
		clock = t.now
	}
	return clock().UTC().Format(time.RFC3339Nano)
}

// DefaultTracePath is where a bare --trace records: one file per launch under
// ~/.gaia/traces, named so runs sort chronologically and name their agent.
func DefaultTracePath(agentID string, at time.Time) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("cannot locate the home directory to place the trace in: %w "+
			"(pass --trace=<path> to choose one explicitly)", err)
	}
	name := fmt.Sprintf("%s-%s.jsonl", at.Format("20060102-150405"), traceSlug(agentID))
	return filepath.Join(home, ".gaia", "traces", name), nil
}

// traceSlug reduces an agent id to something safe in a filename on every
// platform GAIA ships to.
func traceSlug(agentID string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(strings.TrimSpace(agentID)) {
		switch {
		case r >= 'a' && r <= 'z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '-' || r == '_':
			b.WriteRune(r)
		default:
			b.WriteRune('-')
		}
	}
	if b.Len() == 0 {
		return "agent"
	}
	return b.String()
}
