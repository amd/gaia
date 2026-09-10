package event

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// readTrace parses a trace file into its records, failing the test on any line
// that is not a single valid JSON object — one-event-per-line is the format's
// whole contract.
func readTrace(t *testing.T, path string) []traceRecord {
	t.Helper()
	f, err := os.Open(path) // #nosec G304 -- test-owned temp path
	if err != nil {
		t.Fatalf("open trace: %v", err)
	}
	defer f.Close()

	var out []traceRecord
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var rec traceRecord
		if err := json.Unmarshal(sc.Bytes(), &rec); err != nil {
			t.Fatalf("trace line is not valid JSON: %v (line: %s)", err, sc.Text())
		}
		out = append(out, rec)
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("scan trace: %v", err)
	}
	return out
}

func TestTraceWriterRoundTripsCanonicalEvents(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}

	// The exact trajectory #3576 needed and could not produce: the call with its
	// ARGUMENTS, and the result payload showing it found nothing.
	frames := []string{
		`{"type":"status","message":"Searching"}`,
		`{"type":"tool_call","tool":"find_files","args":{"pattern":"*.go","path":"tui/internal"}}`,
		`{"type":"tool_result","tool":"find_files","data":{"status":"success","count":0,"files":[]}}`,
		`{"type":"final","answer":"Zero"}`,
	}
	for _, f := range frames {
		if err := w.Write([]byte(f)); err != nil {
			t.Fatalf("Write(%s): %v", f, err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	recs := readTrace(t, path)
	if len(recs) != len(frames) {
		t.Fatalf("got %d records, want %d", len(recs), len(frames))
	}
	for i, rec := range recs {
		if rec.Seq != i+1 {
			t.Errorf("record %d: seq = %d, want %d", i, rec.Seq, i+1)
		}
		if _, err := time.Parse(time.RFC3339Nano, rec.At); err != nil {
			t.Errorf("record %d: at %q is not RFC3339: %v", i, rec.At, err)
		}
		if rec.Unparsed != "" {
			t.Errorf("record %d: valid JSON was filed as unparsed: %s", i, rec.Unparsed)
		}
	}

	// The payload must survive verbatim, not just structurally — the arguments
	// and the zero count are the evidence.
	var call struct {
		Tool string            `json:"tool"`
		Args map[string]string `json:"args"`
	}
	if err := json.Unmarshal(recs[1].Event, &call); err != nil {
		t.Fatalf("decode tool_call: %v", err)
	}
	if call.Tool != "find_files" || call.Args["path"] != "tui/internal" || call.Args["pattern"] != "*.go" {
		t.Errorf("tool_call arguments lost: %+v", call)
	}

	var result struct {
		Data struct {
			Status string `json:"status"`
			Count  int    `json:"count"`
		} `json:"data"`
	}
	if err := json.Unmarshal(recs[2].Event, &result); err != nil {
		t.Fatalf("decode tool_result: %v", err)
	}
	if result.Data.Status != "success" || result.Data.Count != 0 {
		t.Errorf("tool_result payload lost: %+v", result.Data)
	}
}

func TestTraceWriterRecordsMalformedPayloads(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	// Neither of these parses as a canonical event. Dropping them would hide
	// exactly the frame a trace is most wanted for.
	bad := []string{`{"type":"tool_call",`, `not json at all`}
	for _, b := range bad {
		if err := w.Write([]byte(b)); err != nil {
			t.Fatalf("Write(%q): %v", b, err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	recs := readTrace(t, path)
	if len(recs) != len(bad) {
		t.Fatalf("got %d records, want %d — a bad frame was dropped", len(recs), len(bad))
	}
	for i, rec := range recs {
		if rec.Unparsed != bad[i] {
			t.Errorf("record %d: unparsed = %q, want %q", i, rec.Unparsed, bad[i])
		}
		if len(rec.Event) != 0 {
			t.Errorf("record %d: unreadable payload was filed as an event: %s", i, rec.Event)
		}
	}
}

// Encoding a Go string to JSON turns every bad byte into U+FFFD, so a payload
// that is not valid UTF-8 has to go out base64 — an agent emitting binary
// garbage is exactly the failure a trace is supposed to explain.
func TestTraceWriterPreservesNonUTF8Payloads(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	raw := []byte{'g', 'a', 'r', 'b', 0xff, 0xfe, '{', '"'}
	if err := w.Write(raw); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	recs := readTrace(t, path)
	if len(recs) != 1 {
		t.Fatalf("got %d records, want 1", len(recs))
	}
	if recs[0].Unparsed != "" {
		t.Errorf("bad bytes went through the string field and were mangled: %q", recs[0].Unparsed)
	}
	got, err := base64.StdEncoding.DecodeString(recs[0].UnparsedB64)
	if err != nil {
		t.Fatalf("unparsed_b64 is not base64: %v", err)
	}
	if !bytes.Equal(got, raw) {
		t.Errorf("payload changed: got %x, want %x", got, raw)
	}
}

// A launch can die after the trace opens — a refused port, an unmet
// precondition — and ~/.gaia/traces is never pruned, so an empty file must not
// be left behind.
func TestTraceWriterRemovesItsOwnEmptyFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Errorf("a trace that recorded nothing was left on disk (stat err: %v)", err)
	}
}

// ...but only a file it created. Reopening an EXISTING trace and writing
// nothing must never delete the earlier run's records.
func TestTraceWriterKeepsAnExistingTraceItDidNotWriteTo(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	first, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	if err := first.Write([]byte(`{"type":"final","answer":"kept"}`)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if err := first.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	second, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	if err := second.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if recs := readTrace(t, path); len(recs) != 1 {
		t.Fatalf("the earlier run's trace was destroyed: %d records left", len(recs))
	}
}

func TestTraceWriterKeepsOneEventPerLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	pretty := "{\n  \"type\": \"status\",\n  \"message\": \"line\\nbreak\"\n}"
	if err := w.Write([]byte(pretty)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if err := w.Write([]byte(`{"type":"final","answer":"done"}`)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	raw, err := os.ReadFile(path) // #nosec G304 -- test-owned temp path
	if err != nil {
		t.Fatalf("read trace: %v", err)
	}
	if got := strings.Count(strings.TrimRight(string(raw), "\n"), "\n"); got != 1 {
		t.Errorf("a pretty-printed frame was not compacted: %d newlines between 2 records\n%s", got, raw)
	}
	if len(readTrace(t, path)) != 2 {
		t.Errorf("want 2 records")
	}
}

func TestTraceWriterFlushesPerEvent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	defer w.Close()
	if err := w.Write([]byte(`{"type":"status","message":"still running"}`)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	// Read WITHOUT closing: a run that crashes mid-turn must still leave behind
	// everything it had received.
	if recs := readTrace(t, path); len(recs) != 1 {
		t.Fatalf("got %d records before Close, want 1 — the event was still buffered", len(recs))
	}
}

func TestTraceWriterAppendsAcrossOpens(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	for i := 0; i < 2; i++ {
		w, err := NewTraceWriter(path)
		if err != nil {
			t.Fatalf("NewTraceWriter #%d: %v", i, err)
		}
		if err := w.Write([]byte(`{"type":"status"}`)); err != nil {
			t.Fatalf("Write #%d: %v", i, err)
		}
		if err := w.Close(); err != nil {
			t.Fatalf("Close #%d: %v", i, err)
		}
	}
	if recs := readTrace(t, path); len(recs) != 2 {
		t.Errorf("got %d records, want 2 — reopening truncated the trace", len(recs))
	}
}

func TestNewTraceWriterFailsRatherThanDegrades(t *testing.T) {
	// A regular file where a directory has to be: MkdirAll cannot make it, on
	// any platform GAIA ships to.
	blocker := filepath.Join(t.TempDir(), "not-a-dir")
	if err := os.WriteFile(blocker, []byte("x"), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	w, err := NewTraceWriter(filepath.Join(blocker, "run.jsonl"))
	if err == nil {
		w.Close()
		t.Fatal("an unwritable trace path was accepted — a trace nobody can write must be a startup error")
	}
	if !strings.Contains(err.Error(), "--trace") {
		t.Errorf("the error does not say how to fix it: %v", err)
	}
}

func TestNewTraceWriterRejectsEmptyPath(t *testing.T) {
	if _, err := NewTraceWriter("   "); err == nil {
		t.Fatal("an empty trace path was accepted")
	}
}

func TestNilTraceWriterIsTracingOff(t *testing.T) {
	var w *TraceWriter
	if err := w.Write([]byte(`{"type":"status"}`)); err != nil {
		t.Errorf("Write on a nil writer: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Errorf("Close on a nil writer: %v", err)
	}
	if got := w.Path(); got != "" {
		t.Errorf("Path() on a nil writer = %q, want \"\"", got)
	}
}

func TestTraceWriterCloseIsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "run.jsonl")
	w, err := NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("first Close: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Errorf("second Close: %v", err)
	}
	// A write after close is a no-op, not a panic on a closed file.
	if err := w.Write([]byte(`{"type":"status"}`)); err != nil {
		t.Errorf("Write after Close: %v", err)
	}
}

func TestDefaultTracePathNamesTheRunAndAgent(t *testing.T) {
	at := time.Date(2026, 9, 8, 18, 4, 19, 0, time.UTC)
	got, err := DefaultTracePath("gaia", at)
	if err != nil {
		t.Fatalf("DefaultTracePath: %v", err)
	}
	if base := filepath.Base(got); base != "20260908-180419-gaia.jsonl" {
		t.Errorf("filename = %q, want %q", base, "20260908-180419-gaia.jsonl")
	}
	if dir := filepath.Base(filepath.Dir(got)); dir != "traces" {
		t.Errorf("parent directory = %q, want \"traces\"", dir)
	}
}

func TestTraceSlugIsFilenameSafe(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"gaia", "gaia"},
		{"Email", "email"},
		{"doc/qa", "doc-qa"},
		{"a b:c", "a-b-c"},
		{"", "agent"},
		{"///", "---"},
	} {
		if got := traceSlug(tc.in); got != tc.want {
			t.Errorf("traceSlug(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
