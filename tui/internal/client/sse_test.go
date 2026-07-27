package client

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

// sidecarBearer stands in for the sidecar token the daemon's /ensure response
// carries. The TUI must never present it on any request.
const sidecarBearer = "SIDECAR-BEARER-MUST-NOT-LEAK"

// fakeRelay is an httptest server standing in for the GAIA daemon: the status
// probe, the sidecar ensure, and the /v1/<agent>/query SSE relay.
type fakeRelay struct {
	t   *testing.T
	srv *httptest.Server
	dir string

	// stream writes the SSE body for one run. Set per test.
	stream func(w http.ResponseWriter, flush func(), body queryRequest)
	// queryStatus, when non-zero, is returned instead of a stream.
	queryStatus int
	// contractVersion is what GET /v1/<agent>/version reports. Empty means the
	// route 404s, like a sidecar predating it.
	contractVersion string
	// strictBody replicates the sidecar's pydantic `extra="forbid"`: an unknown
	// request field is a 422, not an ignored key. This is what makes a published
	// older sidecar reject a field a newer TUI invented.
	strictBody bool
	// onEnsure runs after a successful ensure — used to simulate a daemon
	// restart (and therefore a token rotation) mid-Send.
	onEnsure func()

	mu          sync.Mutex
	token       string
	queries     []queryRequest
	rawBodies   []string
	cancelled   []string
	auths       []string
	versionHits int
}

func newFakeRelay(t *testing.T) *fakeRelay {
	t.Helper()

	dir := t.TempDir()
	t.Setenv(daemon.EnvHome, dir)

	f := &fakeRelay{t: t, dir: dir, token: "token-A", contractVersion: "2.6"}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)

	if f.port() == daemon.ReservedPort {
		t.Fatalf("test server bound the reserved port %d", daemon.ReservedPort)
	}
	f.writeInstance("token-A")
	return f
}

func (f *fakeRelay) port() int {
	u, err := url.Parse(f.srv.URL)
	if err != nil {
		f.t.Fatalf("parse server URL: %v", err)
	}
	p, err := strconv.Atoi(u.Port())
	if err != nil {
		f.t.Fatalf("parse port: %v", err)
	}
	return p
}

func (f *fakeRelay) writeInstance(token string) {
	f.t.Helper()
	payload := map[string]any{
		"pid": os.Getpid(), "port": f.port(), "token": token,
		"host": "127.0.0.1", "api_version": "1.1", "service": "gaia-daemon",
		"started_at": 1.0,
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		f.t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(f.dir, "instance.json"), raw, 0o600); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fakeRelay) rotateToken(next string) {
	f.mu.Lock()
	f.token = next
	f.mu.Unlock()
	f.writeInstance(next)
}

func (f *fakeRelay) handle(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	token := f.token
	f.auths = append(f.auths, r.Header.Get("Authorization"))
	f.mu.Unlock()

	if r.Header.Get("Authorization") != "Bearer "+token {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"detail":"invalid or expired client token"}`))
		return
	}

	switch {
	case r.URL.Path == "/daemon/v1/status":
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"service":"gaia-daemon","pid":%d}`, os.Getpid())

	case strings.HasSuffix(r.URL.Path, "/ensure"):
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"port":51999,"token":%q,"state":"running"}`, sidecarBearer)
		if f.onEnsure != nil {
			f.onEnsure()
		}

	case strings.HasSuffix(r.URL.Path, "/version"):
		f.mu.Lock()
		f.versionHits++
		f.mu.Unlock()
		if f.contractVersion == "" {
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"detail":"no route"}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"apiVersion":%q,"agentVersion":"0.5.0"}`, f.contractVersion)

	case strings.HasSuffix(r.URL.Path, "/cancel"):
		parts := strings.Split(r.URL.Path, "/")
		f.mu.Lock()
		f.cancelled = append(f.cancelled, parts[len(parts)-2])
		f.mu.Unlock()
		w.WriteHeader(http.StatusOK)

	case strings.HasSuffix(r.URL.Path, "/query"):
		var body queryRequest
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, &body); err != nil {
			f.t.Errorf("query body is not valid JSON: %v (%s)", err, raw)
		}
		f.mu.Lock()
		f.queries = append(f.queries, body)
		f.rawBodies = append(f.rawBodies, string(raw))
		f.mu.Unlock()

		if f.strictBody {
			// The accepted field set is the one THIS peer's contract declares —
			// 2.6 knows can_answer_questions, older versions do not. Modelling
			// only one of the two would make the fake agree with the client by
			// construction, which is how the 422 shipped in the first place.
			dec := json.NewDecoder(strings.NewReader(string(raw)))
			dec.DisallowUnknownFields()
			var derr error
			if contractAtLeast(f.contractVersion, 2, 6) {
				var strict struct {
					Query              string `json:"query"`
					RunID              string `json:"run_id"`
					Context            []Turn `json:"context"`
					Model              string `json:"model"`
					MaxSteps           int    `json:"max_steps"`
					CanAnswerQuestions *bool  `json:"can_answer_questions"`
				}
				derr = dec.Decode(&strict)
			} else {
				var strict struct {
					Query    string `json:"query"`
					RunID    string `json:"run_id"`
					Context  []Turn `json:"context"`
					Model    string `json:"model"`
					MaxSteps int    `json:"max_steps"`
				}
				derr = dec.Decode(&strict)
			}
			if derr != nil {
				w.WriteHeader(http.StatusUnprocessableEntity)
				fmt.Fprintf(w, `{"detail":[{"type":"extra_forbidden","msg":%q}]}`, derr.Error())
				return
			}
		}

		if f.queryStatus != 0 {
			w.WriteHeader(f.queryStatus)
			_, _ = w.Write([]byte(`{"detail":"the email sidecar is not running"}`))
			return
		}
		if accept := r.Header.Get("Accept"); accept != "text/event-stream" {
			f.t.Errorf("Accept = %q, want text/event-stream", accept)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		flusher, ok := w.(http.Flusher)
		if !ok {
			f.t.Fatal("test server response writer is not a Flusher")
		}
		f.stream(w, flusher.Flush, body)

	default:
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"detail":"no route"}`))
	}
}

// versionProbes counts GET /v1/<agent>/version calls, so the probe can be
// asserted to happen once per client rather than once per turn.
func (f *fakeRelay) versionProbes() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.versionHits
}

func (f *fakeRelay) lastRawBody() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.rawBodies) == 0 {
		f.t.Fatal("no query body was received")
	}
	return f.rawBodies[len(f.rawBodies)-1]
}

func (f *fakeRelay) lastQuery() queryRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.queries) == 0 {
		f.t.Fatal("no query was received")
	}
	return f.queries[len(f.queries)-1]
}

func (f *fakeRelay) client(t *testing.T) *SSEClient {
	t.Helper()
	dc := daemon.New(daemon.Options{
		ProbeTimeout:  2 * time.Second,
		EnsureTimeout: 5 * time.Second,
		StartCommand: func(context.Context) (*exec.Cmd, error) {
			return nil, fmt.Errorf("the test daemon must already be attachable")
		},
		Logf: func(format string, args ...any) { t.Logf(format, args...) },
	})
	return NewSSEClient("email", dc, SSEOptions{
		ReadTimeout: 5 * time.Second,
		Logf:        func(format string, args ...any) { t.Logf(format, args...) },
	})
}

// frame writes one canonical SSE event.
func frame(w io.Writer, payload string) {
	fmt.Fprintf(w, "data: %s\n\n", payload)
}

// collect drains a turn's channel.
func collect(t *testing.T, ch <-chan interface{}) []interface{} {
	t.Helper()
	var got []interface{}
	timeout := time.After(15 * time.Second)
	for {
		select {
		case evt, ok := <-ch:
			if !ok {
				return got
			}
			got = append(got, evt)
		case <-timeout:
			t.Fatalf("timed out draining the event channel after %d events", len(got))
		}
	}
}

func typeNames(events []interface{}) []string {
	names := make([]string, 0, len(events))
	for _, e := range events {
		names = append(names, fmt.Sprintf("%T", e))
	}
	return names
}

// --- happy path -------------------------------------------------------------

func TestSSEClientFullRun(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"Scanning inbox"}`)
		frame(w, `{"type":"tool_call","tool":"pre_scan_inbox","args":{"max":50}}`)
		frame(w, `{"type":"tool_result","tool":"pre_scan_inbox","render":"email_pre_scan","data":{"urgent":[]}}`)
		frame(w, `{"type":"token","delta":"You have "}`)
		frame(w, `{"type":"token","delta":"3 urgent emails."}`)
		frame(w, `{"type":"final","answer":"You have 3 urgent emails.","usage":{"steps":2,"tools_used":1}}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage my inbox")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	want := []string{
		"event.CanonicalStatusEvent",
		"event.CanonicalToolCallEvent",
		"event.CanonicalToolResultEvent",
		"event.CanonicalTokenEvent",
		"event.CanonicalTokenEvent",
		"event.CanonicalFinalEvent",
	}
	if names := typeNames(got); !equalStrings(names, want) {
		t.Fatalf("event sequence = %v, want %v", names, want)
	}

	tr := got[2].(event.CanonicalToolResultEvent)
	if tr.Render != "email_pre_scan" {
		t.Errorf("render hint lost: %+v", tr)
	}
	if !strings.Contains(string(tr.Data), "urgent") {
		t.Errorf("render data lost: %s", tr.Data)
	}

	fin := got[5].(event.CanonicalFinalEvent)
	if fin.Answer != "You have 3 urgent emails." {
		t.Errorf("answer = %q", fin.Answer)
	}
	if u := event.CanonicalUsageOf(fin); u.Steps != 2 || u.ToolsUsed != 1 {
		t.Errorf("usage = %+v", u)
	}

	// The request must carry a v4 run_id and an (empty) context array.
	q := f.lastQuery()
	if len(q.RunID) != 36 {
		t.Errorf("run_id %q is not a uuid4", q.RunID)
	}
	if q.RunID[14] != '4' {
		t.Errorf("run_id %q is not version 4", q.RunID)
	}
	if len(q.Context) != 0 {
		t.Errorf("first turn must push an empty context, got %+v", q.Context)
	}
	if q.Query != "triage my inbox" {
		t.Errorf("query = %q", q.Query)
	}

	// `context` is a REQUIRED array: an empty transcript must serialize as [],
	// never null, or the sidecar rejects the body.
	f.mu.Lock()
	rawBody := f.rawBodies[len(f.rawBodies)-1]
	f.mu.Unlock()
	if !strings.Contains(rawBody, `"context":[]`) {
		t.Errorf("first turn body must carry an empty context array, got %s", rawBody)
	}
}

// The host owns the transcript and pushes it back on every turn.
func TestSSEClientPushesHostOwnedTranscript(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), body queryRequest) {
		frame(w, fmt.Sprintf(`{"type":"final","answer":"reply to %s"}`, body.Query))
		flush()
	}

	c := f.client(t)
	defer c.Close()

	for _, q := range []string{"first", "second"} {
		ch, err := c.Send(context.Background(), q)
		if err != nil {
			t.Fatalf("Send(%q): %v", q, err)
		}
		collect(t, ch)
	}

	q := f.lastQuery()
	if len(q.Context) != 2 {
		t.Fatalf("second turn context = %+v, want 2 entries", q.Context)
	}
	if q.Context[0].Role != "user" || q.Context[0].Content != "first" {
		t.Errorf("context[0] = %+v", q.Context[0])
	}
	if q.Context[1].Role != "assistant" || !strings.Contains(q.Context[1].Content, "first") {
		t.Errorf("context[1] = %+v", q.Context[1])
	}

	if got := c.Transcript(); len(got) != 4 {
		t.Errorf("transcript = %+v, want 4 entries after two turns", got)
	}
	c.ResetTranscript()
	if got := c.Transcript(); len(got) != 0 {
		t.Errorf("ResetTranscript left %+v", got)
	}
}

// A `final` with an empty answer after streamed tokens keeps the streamed text.
func TestSSEClientTranscriptFallsBackToStreamedTokens(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"token","delta":"streamed "}`)
		frame(w, `{"type":"token","delta":"answer"}`)
		frame(w, `{"type":"final","answer":""}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	collect(t, ch)

	tr := c.Transcript()
	if len(tr) != 2 || tr[1].Content != "streamed answer" {
		t.Fatalf("transcript = %+v", tr)
	}
}

// --- terminal-event contract -------------------------------------------------

// A stream that ends without final/error is a FAILURE, not an empty success.
func TestSSEClientStreamWithoutTerminalEventIsAnError(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"working"}`)
		frame(w, `{"type":"token","delta":"half an ans"}`)
		flush()
		// …and the sidecar dies: the stream just ends.
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	if len(got) == 0 {
		t.Fatal("expected events")
	}
	last, ok := got[len(got)-1].(event.CanonicalErrorEvent)
	if !ok {
		t.Fatalf("last event = %T, want a synthesized CanonicalErrorEvent (%v)", got[len(got)-1], typeNames(got))
	}
	if !strings.Contains(last.Detail, "without a terminal") {
		t.Errorf("detail must explain the missing terminal event: %q", last.Detail)
	}
	if !strings.Contains(last.Detail, "gaia daemon status") {
		t.Errorf("detail must be actionable: %q", last.Detail)
	}
	if last.Source != "tui" {
		t.Errorf("synthesized errors must be attributed to the tui, got %q", last.Source)
	}
	// A failed turn must not poison the next one's context.
	if tr := c.Transcript(); len(tr) != 0 {
		t.Errorf("a turn without `final` must not be appended to the transcript: %+v", tr)
	}
}

func TestSSEClientTerminalErrorEvent(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"loading model"}`)
		frame(w, `{"type":"error","detail":"Lemonade Server is not reachable at http://localhost:8000 — run `+"`gaia init`"+`","status":503}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	last, ok := got[len(got)-1].(event.CanonicalErrorEvent)
	if !ok {
		t.Fatalf("last event = %T, want CanonicalErrorEvent", got[len(got)-1])
	}
	if !strings.Contains(last.Detail, "gaia init") {
		t.Errorf("the wire detail must be surfaced verbatim: %q", last.Detail)
	}
	if last.Status != 503 {
		t.Errorf("status = %d, want 503", last.Status)
	}
	if last.Source != "" {
		t.Errorf("a wire error must not be attributed to the tui, got %q", last.Source)
	}
	if tr := c.Transcript(); len(tr) != 0 {
		t.Errorf("an errored turn must not be appended to the transcript: %+v", tr)
	}
}

// --- framing ----------------------------------------------------------------

func TestSSEClientSkipsUnparseableFramesAndHeartbeats(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		fmt.Fprint(w, ": keep-alive\n\n")
		frame(w, `{"type":"status","message":"one"}`)
		frame(w, `{"type":"token", TRUNCATED`)       // not valid JSON
		fmt.Fprint(w, ": another heartbeat\n")       // comment, no blank line
		frame(w, `{"type":"brand_new_event","x":1}`) // unknown canonical type
		frame(w, `{"type":"status","message":"two"}`)
		frame(w, `{"type":"final","answer":"survived"}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	want := []string{
		"event.CanonicalStatusEvent",
		"event.CanonicalMalformedEvent",
		"event.CanonicalUnsupportedEvent",
		"event.CanonicalStatusEvent",
		"event.CanonicalFinalEvent",
	}
	if names := typeNames(got); !equalStrings(names, want) {
		t.Fatalf("event sequence = %v, want %v (heartbeats must be dropped, bad frames surfaced)", names, want)
	}
	if fin := got[4].(event.CanonicalFinalEvent); fin.Answer != "survived" {
		t.Errorf("the run must survive a bad frame, got %+v", fin)
	}
}

// A final frame with no trailing blank line must still be delivered.
func TestSSEClientFlushesTrailingFrameWithoutBlankLine(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"working"}`)
		fmt.Fprint(w, `data: {"type":"final","answer":"no trailing newline"}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	fin, ok := got[len(got)-1].(event.CanonicalFinalEvent)
	if !ok {
		t.Fatalf("last event = %T, want CanonicalFinalEvent (%v)", got[len(got)-1], typeNames(got))
	}
	if fin.Answer != "no trailing newline" {
		t.Errorf("answer = %q", fin.Answer)
	}
}

// Multi-line `data:` fields join with a newline, per the SSE spec.
func TestSSEClientJoinsMultiLineDataFields(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		fmt.Fprint(w, "data: {\"type\":\"final\",\ndata: \"answer\":\"split payload\"}\n\n")
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	fin, ok := got[0].(event.CanonicalFinalEvent)
	if !ok {
		t.Fatalf("event = %T, want CanonicalFinalEvent", got[0])
	}
	if fin.Answer != "split payload" {
		t.Errorf("answer = %q", fin.Answer)
	}
}

// --- auth -------------------------------------------------------------------

// The token rotates on every daemon restart: a 401 mid-Send must re-read
// instance.json and retry exactly once.
func TestSSEClientRetriesOnceAfterTokenRotation(t *testing.T) {
	f := newFakeRelay(t)
	// The daemon restarts right after the ensure — the query then presents a
	// token the new daemon does not know.
	f.onEnsure = func() { f.rotateToken("token-B") }
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"after rotation"}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	fin, ok := got[len(got)-1].(event.CanonicalFinalEvent)
	if !ok {
		t.Fatalf("last event = %T, want CanonicalFinalEvent (%v)", got[len(got)-1], typeNames(got))
	}
	if fin.Answer != "after rotation" {
		t.Errorf("answer = %q", fin.Answer)
	}

	f.mu.Lock()
	auths := append([]string(nil), f.auths...)
	f.mu.Unlock()
	var sawA, sawB bool
	for _, a := range auths {
		switch a {
		case "Bearer token-A":
			sawA = true
		case "Bearer token-B":
			sawB = true
		case "Bearer " + sidecarBearer:
			t.Fatal("the sidecar bearer was presented to the daemon")
		}
	}
	if !sawA || !sawB {
		t.Errorf("expected attempts with both tokens, saw %v", auths)
	}
}

func TestSSEClientNeverPresentsTheSidecarBearer(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"final","answer":"ok"}`)
		flush()
	}

	c := f.client(t)
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	collect(t, ch)

	f.mu.Lock()
	defer f.mu.Unlock()
	for _, a := range f.auths {
		if strings.Contains(a, sidecarBearer) {
			t.Fatalf("the sidecar bearer leaked onto the wire: %q", a)
		}
	}
}

// --- refusals ---------------------------------------------------------------

func TestSSEClientSurfacesRelayRefusal(t *testing.T) {
	f := newFakeRelay(t)
	f.queryStatus = http.StatusServiceUnavailable
	f.stream = func(http.ResponseWriter, func(), queryRequest) {}

	c := f.client(t)
	defer c.Close()

	_, err := c.Send(context.Background(), "triage")
	if err == nil {
		t.Fatal("expected an error when the relay refuses the query")
	}
	if !strings.Contains(err.Error(), "not running") {
		t.Errorf("the relay's detail must be surfaced: %v", err)
	}
	if !strings.Contains(err.Error(), "gaia daemon status") {
		t.Errorf("the error must be actionable: %v", err)
	}
}

func TestSSEClientFailsLoudlyWithNoDaemon(t *testing.T) {
	dir := t.TempDir()
	t.Setenv(daemon.EnvHome, dir)

	dc := daemon.New(daemon.Options{
		ProbeTimeout: time.Second,
		StartCommand: func(context.Context) (*exec.Cmd, error) {
			return nil, fmt.Errorf("no launcher in this test")
		},
	})
	c := NewSSEClient("email", dc, SSEOptions{})
	defer c.Close()

	_, err := c.Send(context.Background(), "triage")
	if err == nil {
		t.Fatal("expected a loud error when no daemon is registered")
	}
	if !strings.Contains(err.Error(), "no launcher in this test") {
		t.Errorf("the start failure must be surfaced: %v", err)
	}
}

// --- cancellation -----------------------------------------------------------

// Cancelling the turn must POST the run's cancel endpoint so the sidecar stops
// between tool steps.
func TestSSEClientCancelPostsToCancelEndpoint(t *testing.T) {
	f := newFakeRelay(t)
	started := make(chan struct{})
	release := make(chan struct{})
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"thinking"}`)
		flush()
		close(started)
		<-release // hold the stream open with no terminal event
	}

	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithCancel(context.Background())
	ch, err := c.Send(ctx, "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	if _, ok := <-ch; !ok {
		t.Fatal("expected the status event before cancelling")
	}
	<-started
	cancel()
	for range ch { // drain
	}
	close(release)

	deadline := time.Now().Add(5 * time.Second)
	for {
		f.mu.Lock()
		n := len(f.cancelled)
		runIDs := append([]string(nil), f.cancelled...)
		f.mu.Unlock()
		if n > 0 {
			if runIDs[0] != f.lastQuery().RunID {
				t.Errorf("cancelled run_id %q, want %q", runIDs[0], f.lastQuery().RunID)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("no cancel was posted within 5s of cancelling the turn")
		}
		time.Sleep(20 * time.Millisecond)
	}

	if tr := c.Transcript(); len(tr) != 0 {
		t.Errorf("a cancelled turn must not be appended to the transcript: %+v", tr)
	}
}

// The read-idle watchdog must abandon a silent stream with an actionable error.
func TestSSEClientReadIdleTimeout(t *testing.T) {
	f := newFakeRelay(t)
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	f.stream = func(w http.ResponseWriter, flush func(), _ queryRequest) {
		frame(w, `{"type":"status","message":"loading model"}`)
		flush()
		<-release
	}

	dc := daemon.New(daemon.Options{
		ProbeTimeout: 2 * time.Second,
		StartCommand: func(context.Context) (*exec.Cmd, error) {
			return nil, fmt.Errorf("unused")
		},
	})
	c := NewSSEClient("email", dc, SSEOptions{ReadTimeout: 300 * time.Millisecond})
	defer c.Close()

	ch, err := c.Send(context.Background(), "triage")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	got := collect(t, ch)

	last, ok := got[len(got)-1].(event.CanonicalErrorEvent)
	if !ok {
		t.Fatalf("last event = %T, want CanonicalErrorEvent (%v)", got[len(got)-1], typeNames(got))
	}
	if !strings.Contains(last.Detail, "sent nothing for") {
		t.Errorf("detail must explain the idle timeout: %q", last.Detail)
	}
}

func TestSSEClientRefusesSendAfterClose(t *testing.T) {
	f := newFakeRelay(t)
	f.stream = func(http.ResponseWriter, func(), queryRequest) {}

	c := f.client(t)
	if err := c.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if _, err := c.Send(context.Background(), "triage"); err == nil {
		t.Fatal("expected an error sending on a closed client")
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
