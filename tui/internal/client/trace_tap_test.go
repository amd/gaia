package client

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
)

// newTestTrace opens a trace writer at path, closed by the test if the test
// itself does not get that far.
func newTestTrace(t *testing.T, path string) *event.TraceWriter {
	t.Helper()
	w, err := event.NewTraceWriter(path)
	if err != nil {
		t.Fatalf("NewTraceWriter: %v", err)
	}
	t.Cleanup(func() { _ = w.Close() })
	return w
}

// tracedLines returns the `event` object of every record in a trace file, as
// compact JSON text.
func tracedLines(t *testing.T, path string) []string {
	t.Helper()
	f, err := os.Open(path) // #nosec G304 -- test-owned temp path
	if err != nil {
		t.Fatalf("open trace: %v", err)
	}
	defer f.Close()

	var out []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		var rec struct {
			Event    json.RawMessage `json:"event"`
			Unparsed string          `json:"unparsed"`
		}
		if err := json.Unmarshal(sc.Bytes(), &rec); err != nil {
			t.Fatalf("trace line is not JSON: %v (%s)", err, sc.Text())
		}
		if len(rec.Event) > 0 {
			out = append(out, string(rec.Event))
			continue
		}
		out = append(out, rec.Unparsed)
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("scan trace: %v", err)
	}
	return out
}

// TestSSEClientTracesEveryFrame is the acceptance shape of #3576: a tool that
// reports success while finding nothing has to be visible in the trace, with
// its ARGUMENTS and its result payload — neither of which the agent-side
// recorder keeps.
func TestSSEClientTracesEveryFrame(t *testing.T) {
	relay := newFakeRelay(t)
	relay.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"Searching"}`)
		frame(w, `{"type":"tool_call","tool":"find_files","args":{"pattern":"*.go","path":"tui/internal"}}`)
		frame(w, `{"type":"tool_result","tool":"find_files","data":{"status":"success","count":0}}`)
		frame(w, `{"type":"final","answer":"Zero"}`)
		flush()
	}

	path := filepath.Join(t.TempDir(), "run.jsonl")
	tw := newTestTrace(t, path)
	c := relay.client(t)
	c.opts.Trace = tw

	ch, err := c.Send(context.Background(), "How many Go files are in tui/internal?")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	collect(t, ch)
	if err := tw.Close(); err != nil {
		t.Fatalf("close trace: %v", err)
	}

	lines := tracedLines(t, path)
	if len(lines) != 4 {
		t.Fatalf("traced %d frames, want 4: %v", len(lines), lines)
	}
	joined := strings.Join(lines, "\n")
	for _, want := range []string{
		`"tool":"find_files"`,
		`"path":"tui/internal"`, // the ARGUMENTS turn_metrics never records
		`"count":0`,             // the payload that makes "Zero" a bug, not an answer
		`"status":"success"`,    // ...while the tool still calls itself successful
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("the trace does not carry %s:\n%s", want, joined)
		}
	}
}

// A frame the parser cannot read is exactly the frame a trace is wanted for, so
// the tap runs BEFORE parsing.
func TestSSEClientTracesUnparseableFrames(t *testing.T) {
	relay := newFakeRelay(t)
	relay.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"tool_call",`)
		frame(w, `{"type":"final","answer":"done"}`)
		flush()
	}

	path := filepath.Join(t.TempDir(), "run.jsonl")
	tw := newTestTrace(t, path)
	c := relay.client(t)
	c.opts.Trace = tw

	ch, err := c.Send(context.Background(), "hi")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	collect(t, ch)
	if err := tw.Close(); err != nil {
		t.Fatalf("close trace: %v", err)
	}

	lines := tracedLines(t, path)
	if len(lines) != 2 {
		t.Fatalf("traced %d frames, want 2 — the bad frame was dropped: %v", len(lines), lines)
	}
	if lines[0] != `{"type":"tool_call",` {
		t.Errorf("the unparseable frame was not preserved verbatim: %q", lines[0])
	}
}

// The flagship's transport is chosen by install state, so the subprocess half
// has to record too — otherwise --trace writes an empty file on the very launch
// the acceptance test uses.
func TestSubprocessClientTracesEveryLine(t *testing.T) {
	bin := buildMockAgent(t)
	path := filepath.Join(t.TempDir(), "run.jsonl")
	tw := newTestTrace(t, path)

	c := NewSubprocessClient(bin, nil, false).WithTrace(tw)
	defer c.Close()

	ch, err := c.Send(context.Background(), "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	for range ch {
	}
	if err := tw.Close(); err != nil {
		t.Fatalf("close trace: %v", err)
	}

	lines := tracedLines(t, path)
	if len(lines) != 5 {
		t.Fatalf("traced %d lines, want the mock agent's 5: %v", len(lines), lines)
	}
	if !strings.Contains(strings.Join(lines, "\n"), `"tool":"bash"`) {
		t.Errorf("the tool line was not traced: %v", lines)
	}
}

// Tracing off is the default, and every tap must accept it without a nil check
// at the call site.
func TestTransportsRunUntracedByDefault(t *testing.T) {
	bin := buildMockAgent(t)
	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	ch, err := c.Send(context.Background(), "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	var n int
	for range ch {
		n++
	}
	if n == 0 {
		t.Error("no events were delivered with tracing off")
	}
}
