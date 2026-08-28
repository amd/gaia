package control

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

// ── harness ─────────────────────────────────────────────────────────

// testModel is a stand-in root model: it echoes what it received and reports a
// snapshot, which is all the control API reads.
type testModel struct {
	lines []string
	snap  Snapshot
}

func newTestModel() testModel {
	return testModel{
		lines: []string{"G A I A"},
		snap:  Snapshot{View: ViewPreflight, Agent: "gaia", Blocker: "binary"},
	}
}

func (m testModel) Init() tea.Cmd { return nil }

func (m testModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	lines := append([]string{}, m.lines...)
	switch msg := msg.(type) {
	case tea.KeyMsg:
		lines = append(lines, "key:"+msg.String())
		switch msg.String() {
		case "enter":
			m.snap = Snapshot{View: "chat", Agent: "email"}
		case "esc":
			m.snap = newTestModel().snap
		case "tab":
			m.snap.Overlay = "help"
		}
	case tea.WindowSizeMsg:
		lines = append(lines, fmt.Sprintf("size:%dx%d", msg.Width, msg.Height))
	}
	m.lines = lines
	return m, nil
}

func (m testModel) View() string { return strings.Join(m.lines, "\n") }

func (m testModel) ControlSnapshot() Snapshot { return m.snap }

// fakeProgram stands in for *tea.Program: Send only QUEUES the message, and a
// separate goroutine runs the Update+View loop — the same asynchrony the real
// program has. A synchronous fake would hide every settle/ordering bug, which
// is exactly the class of bug this server has to get right.
type fakeProgram struct {
	msgs  chan tea.Msg
	done  chan struct{}
	stop  chan struct{}
	model tea.Model

	// delay is applied before each message is processed, to widen the window a
	// caller could observe a half-applied batch in.
	delay time.Duration
}

func newFakeProgram(model tea.Model, delay time.Duration) *fakeProgram {
	f := &fakeProgram{
		msgs:  make(chan tea.Msg, 256),
		done:  make(chan struct{}),
		stop:  make(chan struct{}),
		model: model,
		delay: delay,
	}
	go f.loop()
	return f
}

func (f *fakeProgram) loop() {
	defer close(f.done)
	for {
		select {
		case <-f.stop:
			return
		case msg := <-f.msgs:
			if f.delay > 0 {
				time.Sleep(f.delay)
			}
			next, _ := f.model.Update(msg)
			f.model = next
			_ = next.View() // the event loop renders after every message
		}
	}
}

func (f *fakeProgram) Send(msg tea.Msg) { f.msgs <- msg }

func (f *fakeProgram) Close() {
	close(f.stop)
	<-f.done
}

func newTestServer(t *testing.T) (*Server, *State) {
	t.Helper()
	t.Setenv(EnvHome, t.TempDir())

	return newTestServerWithDelay(t, 0)
}

func newTestServerWithDelay(t *testing.T, delay time.Duration) (*Server, *State) {
	t.Helper()
	state := NewState(nil)
	rec := NewRecorder(newTestModel(), state)
	prog := newFakeProgram(rec, delay)
	_ = rec.View() // seed the first frame, as the event loop does on start

	srv, err := Start(prog, state, Options{Version: "test"})
	if err != nil {
		prog.Close()
		t.Fatalf("Start: %v", err)
	}
	// Mirrors app.go: the event loop is running, so injection is allowed.
	srv.MarkRunning()
	t.Cleanup(func() {
		srv.MarkStopped()
		if err := srv.Stop(); err != nil {
			t.Errorf("Stop: %v", err)
		}
		prog.Close()
	})
	return srv, state
}

func request(t *testing.T, srv *Server, method, path string, body any, token string) (int, map[string]any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal request: %v", err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, srv.BaseURL()+APIPrefix+path, reader)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if token != "" {
		req.Header.Set("Authorization", AuthScheme+" "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	var decoded map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&decoded); err != nil {
		t.Fatalf("%s %s: cannot decode response: %v", method, path, err)
	}
	return resp.StatusCode, decoded
}

func errorField(t *testing.T, body map[string]any, field string) string {
	t.Helper()
	envelope, ok := body["error"].(map[string]any)
	if !ok {
		t.Fatalf("response has no error envelope: %v", body)
	}
	value, _ := envelope[field].(string)
	return value
}

// ── auth ────────────────────────────────────────────────────────────

func TestAuthRejectsMissingAndWrongToken(t *testing.T) {
	srv, _ := newTestServer(t)

	for _, tc := range []struct{ name, token string }{
		{"no token", ""},
		{"wrong token", "0000000000000000000000000000000000000000000000000000000000000000"},
		{"prefix of the real token", srv.Token()[:16]},
	} {
		status, body := request(t, srv, http.MethodGet, "/status", nil, tc.token)
		if status != http.StatusUnauthorized {
			t.Errorf("%s: status = %d, want 401", tc.name, status)
		}
		if code := errorField(t, body, "code"); code != "unauthorized" {
			t.Errorf("%s: error code = %q, want %q", tc.name, code, "unauthorized")
		}
		if hint := errorField(t, body, "hint"); !strings.Contains(hint, "Bearer") {
			t.Errorf("%s: hint %q should name the header to send", tc.name, hint)
		}
	}
}

func TestAuthNeverLeaksTheToken(t *testing.T) {
	srv, _ := newTestServer(t)
	_, body := request(t, srv, http.MethodGet, "/status", nil, "")
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(string(raw), srv.Token()) {
		t.Error("the 401 response echoed the real token")
	}
}

// ── status / screen ─────────────────────────────────────────────────

func TestStatusReportsServiceAndState(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodGet, "/status", nil, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("status = %d, want 200 (%v)", status, body)
	}
	if body["service"] != ServiceID {
		t.Errorf("service = %v, want %q", body["service"], ServiceID)
	}
	if body["api_version"] != APIVersion {
		t.Errorf("api_version = %v, want %q", body["api_version"], APIVersion)
	}
	if body["running"] != true {
		t.Errorf("running = %v, want true", body["running"])
	}
	state, ok := body["state"].(map[string]any)
	if !ok {
		t.Fatalf("status has no state object: %v", body)
	}
	if state["view"] != ViewPreflight || state["blocker"] != "binary" {
		t.Errorf("state = %v, want the preflight view blocked on the binary row", state)
	}
}

func TestScreenFormats(t *testing.T) {
	srv, state := newTestServer(t)
	state.recordFrame("\x1b[1mAgent Hub\x1b[0m")

	status, body := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("screen: status = %d (%v)", status, body)
	}
	if body["format"] != "plain" {
		t.Errorf("default format = %v, want plain", body["format"])
	}
	if screen, _ := body["screen"].(string); screen != "Agent Hub" {
		t.Errorf("plain screen = %q, want %q", screen, "Agent Hub")
	}

	_, ansiBody := request(t, srv, http.MethodGet, "/screen?format=ansi", nil, srv.Token())
	if screen, _ := ansiBody["screen"].(string); !strings.Contains(screen, "\x1b[1m") {
		t.Errorf("ansi screen = %q, want the escape sequences intact", screen)
	}

	badStatus, badBody := request(t, srv, http.MethodGet, "/screen?format=html", nil, srv.Token())
	if badStatus != http.StatusBadRequest {
		t.Errorf("format=html status = %d, want 400", badStatus)
	}
	if hint := errorField(t, badBody, "hint"); !strings.Contains(hint, "plain") {
		t.Errorf("hint %q should name the valid formats", hint)
	}
}

// ── keys / text / resize ────────────────────────────────────────────

func TestSendKeysReachesTheModel(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"tab", "down", "enter"}}, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("keys: status = %d (%v)", status, body)
	}
	if body["sent"] != float64(3) {
		t.Errorf("sent = %v, want 3", body["sent"])
	}

	_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	text, _ := screen["screen"].(string)
	for _, want := range []string{"key:tab", "key:down", "key:enter"} {
		if !strings.Contains(text, want) {
			t.Errorf("screen %q is missing %q — the key did not reach the model as a named key", text, want)
		}
	}
	if strings.Contains(text, "key:t\nkey:a\nkey:b") {
		t.Error("\"tab\" was delivered as three runes")
	}
}

func TestSendKeysRejectsUnknownName(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"enter", "supertab"}}, srv.Token())
	if status != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", status)
	}
	if code := errorField(t, body, "code"); code != "unknown_key" {
		t.Errorf("code = %q, want unknown_key", code)
	}
	if msg := errorField(t, body, "message"); !strings.Contains(msg, "supertab") {
		t.Errorf("message %q should quote the offending key", msg)
	}
	if hint := errorField(t, body, "hint"); !strings.Contains(hint, "shift+tab") {
		t.Errorf("hint %q should list the supported names", hint)
	}

	// Nothing may have been injected: the whole batch is validated first.
	_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	if text, _ := screen["screen"].(string); strings.Contains(text, "key:enter") {
		t.Error("a rejected batch still injected its valid prefix")
	}
}

func TestSendKeysValidatesShape(t *testing.T) {
	srv, _ := newTestServer(t)

	status, body := request(t, srv, http.MethodPost, "/keys", map[string]any{"keys": []string{}}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "no_keys" {
		t.Errorf("empty keys: status %d code %q, want 400/no_keys", status, errorField(t, body, "code"))
	}

	many := make([]string, 101)
	for i := range many {
		many[i] = "down"
	}
	status, body = request(t, srv, http.MethodPost, "/keys", map[string]any{"keys": many}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "too_many_keys" {
		t.Errorf("101 keys: status %d code %q, want 400/too_many_keys", status, errorField(t, body, "code"))
	}

	status, body = request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"down"}, "delay_ms": 9000}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "bad_delay" {
		t.Errorf("delay 9000: status %d code %q, want 400/bad_delay", status, errorField(t, body, "code"))
	}

	status, _ = request(t, srv, http.MethodGet, "/keys", nil, srv.Token())
	if status != http.StatusMethodNotAllowed {
		t.Errorf("GET /keys: status = %d, want 405", status)
	}

	status, body = request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"down"}, "typo": true}, srv.Token())
	if status != http.StatusBadRequest {
		t.Errorf("unknown field: status = %d, want 400 (%v)", status, body)
	}
}

func TestSendText(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/text",
		map[string]any{"text": "hi you"}, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("text: status = %d (%v)", status, body)
	}
	if body["sent_runes"] != float64(6) {
		t.Errorf("sent_runes = %v, want 6", body["sent_runes"])
	}
	_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	text, _ := screen["screen"].(string)
	if !strings.Contains(text, "key:h") || !strings.Contains(text, "key:y") {
		t.Errorf("screen %q is missing the typed runes", text)
	}

	status, body = request(t, srv, http.MethodPost, "/text", map[string]any{"text": ""}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "no_text" {
		t.Errorf("empty text: status %d code %q, want 400/no_text", status, errorField(t, body, "code"))
	}
}

func TestResize(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 120, "rows": 40}, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("resize: status = %d (%v)", status, body)
	}
	if body["cols"] != float64(120) || body["rows"] != float64(40) {
		t.Errorf("resize returned %v x %v, want 120 x 40", body["cols"], body["rows"])
	}

	_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	if text, _ := screen["screen"].(string); !strings.Contains(text, "size:120x40") {
		t.Errorf("screen %q shows no resize; the model never got the WindowSizeMsg", text)
	}

	status, body = request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 5, "rows": 2}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "bad_size" {
		t.Errorf("tiny size: status %d code %q, want 400/bad_size", status, errorField(t, body, "code"))
	}
}

// A synthetic WindowSizeMsg cannot make the real terminal bigger. Laying out
// wider than it is makes the model emit lines the terminal hard-wraps, which
// shreds the visible screen (blank frame, duplicated status line, lost
// scrollback) while /screen still reports the clean logical frame — so the
// corruption is invisible to the very API you would test with.
func TestResizeRefusesToExceedTheTerminal(t *testing.T) {
	prev := realTerminalSize
	// A fixed, real 120x42 viewport for the whole test — NOT s.state.Size(),
	// which the handler itself mutates on every successful resize. Stubbing
	// the real syscall this way is what catches the ratchet bug: the model's
	// logical size moves, the physical terminal (this stub) does not.
	realTerminalSize = func() (int, int, bool) { return 120, 42, true }
	t.Cleanup(func() { realTerminalSize = prev })

	srv, _ := newTestServer(t)
	if status, body := request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 120, "rows": 40}, srv.Token()); status != http.StatusOK {
		t.Fatalf("seed resize: status = %d (%v)", status, body)
	}

	for _, tc := range []struct {
		name       string
		cols, rows int
	}{
		{"wider", 200, 40},
		{"taller", 120, 55},
		{"both", 200, 55},
	} {
		t.Run(tc.name, func(t *testing.T) {
			status, body := request(t, srv, http.MethodPost, "/resize",
				map[string]any{"cols": tc.cols, "rows": tc.rows}, srv.Token())
			if status != http.StatusConflict {
				t.Fatalf("status = %d, want 409 (%v)", status, body)
			}
			if code := errorField(t, body, "code"); code != "resize_exceeds_terminal" {
				t.Errorf("code = %q, want resize_exceeds_terminal", code)
			}
		})
	}

	// Shrinking stays allowed — it cannot overflow the terminal.
	if status, body := request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 80, "rows": 24}, srv.Token()); status != http.StatusOK {
		t.Errorf("shrink: status = %d (%v), want 200", status, body)
	}

	// The regression this test exists for: after that shrink, the model's
	// OWN logical size (80x24) is now smaller than the real terminal
	// (120x42). Growing back toward the real terminal must still succeed —
	// a boundary keyed off s.state.Size() instead of the real terminal would
	// 409 this forever.
	if status, body := request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 120, "rows": 42}, srv.Token()); status != http.StatusOK {
		t.Errorf("grow back to the real terminal size: status = %d (%v), want 200", status, body)
	}

	// Exactly the real terminal size is not "exceeding" it.
	if status, body := request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 120, "rows": 42}, srv.Token()); status != http.StatusOK {
		t.Errorf("exact match: status = %d (%v), want 200", status, body)
	}
}

// Headless runs (tests, CI, MCP drivers with no attached PTY) have no
// physical viewport to overflow, so realTerminalSize reporting ok=false must
// leave the endpoint free to lay out any size in the normal bad_size range —
// the same behavior the un-stubbed default gets when stdout is not a
// terminal.
func TestResizeIsUnboundedWithoutARealTerminal(t *testing.T) {
	prev := realTerminalSize
	realTerminalSize = func() (int, int, bool) { return 0, 0, false }
	t.Cleanup(func() { realTerminalSize = prev })

	srv, _ := newTestServer(t)
	if status, body := request(t, srv, http.MethodPost, "/resize",
		map[string]any{"cols": 300, "rows": 100}, srv.Token()); status != http.StatusOK {
		t.Errorf("headless grow: status = %d (%v), want 200", status, body)
	}
}

// ── wait ────────────────────────────────────────────────────────────

func TestWaitResolvesImmediately(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/wait",
		map[string]any{"contains": "G A I A", "timeout_ms": 2000}, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("wait: status = %d (%v)", status, body)
	}
	if body["matched"] != true {
		t.Errorf("matched = %v, want true", body["matched"])
	}
}

func TestWaitResolvesOnLaterChange(t *testing.T) {
	srv, _ := newTestServer(t)

	go func() {
		time.Sleep(150 * time.Millisecond)
		request(t, srv, http.MethodPost, "/keys", map[string]any{"keys": []string{"enter"}}, srv.Token())
	}()

	start := time.Now()
	status, body := request(t, srv, http.MethodPost, "/wait",
		map[string]any{"state": map[string]any{"view": "chat", "agent": "email"}, "timeout_ms": 5000}, srv.Token())
	elapsed := time.Since(start)

	if status != http.StatusOK {
		t.Fatalf("wait: status = %d (%v)", status, body)
	}
	if elapsed < 100*time.Millisecond {
		t.Errorf("wait returned in %s — it cannot have observed the change", elapsed)
	}
	if elapsed > 3*time.Second {
		t.Errorf("wait took %s — it is not being woken by the state change", elapsed)
	}
	state, _ := body["state"].(map[string]any)
	if state["view"] != "chat" {
		t.Errorf("resolved state = %v, want the chat view", state)
	}
}

func TestWaitTimeoutReportsTheActualScreen(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/wait",
		map[string]any{"contains": "Inbox triaged", "timeout_ms": 200}, srv.Token())
	if status != http.StatusRequestTimeout {
		t.Fatalf("status = %d, want 408 (%v)", status, body)
	}
	if code := errorField(t, body, "code"); code != "wait_timeout" {
		t.Errorf("code = %q, want wait_timeout", code)
	}
	if msg := errorField(t, body, "message"); !strings.Contains(msg, "Inbox triaged") {
		t.Errorf("message %q should quote what was being waited for", msg)
	}
	screen := errorField(t, body, "screen")
	if !strings.Contains(screen, "G A I A") {
		t.Errorf("timeout screen = %q — a timeout must report what the screen actually contained", screen)
	}
	envelope, _ := body["error"].(map[string]any)
	if envelope["state"] == nil {
		t.Error("timeout should also report the state snapshot")
	}
}

func TestWaitAbsentMatcher(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodPost, "/wait",
		map[string]any{"absent": "Loading...", "timeout_ms": 500}, srv.Token())
	if status != http.StatusOK || body["matched"] != true {
		t.Errorf("absent matcher: status %d matched %v, want 200/true", status, body["matched"])
	}
}

func TestWaitValidatesMatchers(t *testing.T) {
	srv, _ := newTestServer(t)

	status, body := request(t, srv, http.MethodPost, "/wait", map[string]any{"timeout_ms": 100}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "no_condition" {
		t.Errorf("no condition: status %d code %q, want 400/no_condition", status, errorField(t, body, "code"))
	}

	status, body = request(t, srv, http.MethodPost, "/wait",
		map[string]any{"state": map[string]any{"vieww": "chat"}}, srv.Token())
	if status != http.StatusBadRequest || errorField(t, body, "code") != "bad_state_matcher" {
		t.Errorf("typo'd state key: status %d code %q, want 400/bad_state_matcher", status, errorField(t, body, "code"))
	}
	if hint := errorField(t, body, "hint"); !strings.Contains(hint, "blocker") {
		t.Errorf("hint %q should list the supported state keys", hint)
	}

	status, body = request(t, srv, http.MethodPost, "/wait",
		map[string]any{"state": map[string]any{"streaming": "yes"}}, srv.Token())
	if status != http.StatusBadRequest {
		t.Errorf("wrong matcher type: status = %d, want 400 (%v)", status, body)
	}

	status, _ = request(t, srv, http.MethodPost, "/wait",
		map[string]any{"contains": "x", "timeout_ms": 999999999}, srv.Token())
	if status != http.StatusBadRequest {
		t.Errorf("absurd timeout: status = %d, want 400", status)
	}
}

// ── frames ──────────────────────────────────────────────────────────

func TestFramesEndpoint(t *testing.T) {
	srv, _ := newTestServer(t)
	request(t, srv, http.MethodPost, "/keys", map[string]any{"keys": []string{"tab", "down"}}, srv.Token())

	status, body := request(t, srv, http.MethodGet, "/frames?since=0&limit=50", nil, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("frames: status = %d (%v)", status, body)
	}
	frames, ok := body["frames"].([]any)
	if !ok || len(frames) < 2 {
		t.Fatalf("frames = %v, want at least the two renders the keys caused", body["frames"])
	}
	last, _ := frames[len(frames)-1].(map[string]any)
	if screen, _ := last["screen"].(string); !strings.Contains(screen, "key:down") {
		t.Errorf("last frame = %q, want the down key visible", screen)
	}

	status, body = request(t, srv, http.MethodGet, "/frames?since=abc", nil, srv.Token())
	if status != http.StatusBadRequest {
		t.Errorf("since=abc: status = %d, want 400 (%v)", status, body)
	}
}

// ── discovery file ──────────────────────────────────────────────────

func TestDiscoveryFileLifecycle(t *testing.T) {
	dir := t.TempDir()
	t.Setenv(EnvHome, dir)

	state := NewState(nil)
	rec := NewRecorder(newTestModel(), state)
	prog := newFakeProgram(rec, 0)
	defer prog.Close()
	srv, err := Start(prog, state, Options{Version: "test"})
	if err != nil {
		t.Fatalf("Start: %v", err)
	}

	info, err := ReadInfo()
	if err != nil {
		t.Fatalf("ReadInfo: %v", err)
	}
	if info == nil {
		t.Fatal("Start did not publish a discovery file")
	}
	if info.Port != srv.Port() || info.Service != ServiceID || info.Token != srv.Token() {
		t.Errorf("discovery record = %+v, want port %d service %q and the live token", info, srv.Port(), ServiceID)
	}
	if info.Host != Host {
		t.Errorf("host = %q, want %q — the control server must never advertise a non-loopback address", info.Host, Host)
	}

	if runtime.GOOS != "windows" {
		path, _ := FilePath()
		stat, err := os.Stat(path)
		if err != nil {
			t.Fatalf("stat: %v", err)
		}
		if perm := stat.Mode().Perm(); perm != 0o600 {
			t.Errorf("discovery file mode = %o, want 600 — it holds the bearer token", perm)
		}
	}

	if err := srv.Stop(); err != nil {
		t.Fatalf("Stop: %v", err)
	}
	after, err := ReadInfo()
	if err != nil {
		t.Fatalf("ReadInfo after stop: %v", err)
	}
	if after != nil {
		t.Error("Stop left the discovery file behind; a client would trust a dead TUI")
	}
}

func TestRemoveInfoLeavesANewerRegistrationAlone(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	if err := WriteInfo(Info{PID: 4242, Port: 9999, Token: "t", Service: ServiceID}); err != nil {
		t.Fatalf("WriteInfo: %v", err)
	}
	if err := RemoveInfo(1); err != nil {
		t.Fatalf("RemoveInfo: %v", err)
	}
	info, err := ReadInfo()
	if err != nil {
		t.Fatalf("ReadInfo: %v", err)
	}
	if info == nil || info.PID != 4242 {
		t.Error("RemoveInfo deleted a registration owned by a different pid")
	}
}

func TestReadInfoOnMissingAndMalformedFile(t *testing.T) {
	dir := t.TempDir()
	t.Setenv(EnvHome, dir)

	info, err := ReadInfo()
	if err != nil || info != nil {
		t.Errorf("ReadInfo with no file = (%v, %v), want (nil, nil)", info, err)
	}

	path, _ := FilePath()
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := ReadInfo(); err == nil {
		t.Error("a malformed discovery file must be an error, not a silently empty result")
	} else if !strings.Contains(err.Error(), "malformed") {
		t.Errorf("error %q should say the file is malformed", err)
	}
}

func TestStartRejectsTheReservedPort(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	state := NewState(nil)
	rec := NewRecorder(newTestModel(), state)
	prog := newFakeProgram(rec, 0)
	defer prog.Close()
	_, err := Start(prog, state, Options{Port: ReservedPort})
	if err == nil {
		t.Fatalf("Start bound the reserved port %d", ReservedPort)
	}
	if !strings.Contains(err.Error(), fmt.Sprint(ReservedPort)) {
		t.Errorf("error %q should name the reserved port", err)
	}
	info, _ := ReadInfo()
	if info != nil {
		t.Error("a rejected Start still published a discovery file")
	}
}

func TestStartRequiresASenderAndState(t *testing.T) {
	t.Setenv(EnvHome, t.TempDir())
	if _, err := Start(nil, NewState(nil), Options{}); err == nil {
		t.Error("Start with no program should fail loudly")
	}
	if _, err := Start(newFakeProgram(newTestModel(), 0), nil, Options{}); err == nil {
		t.Error("Start with no state should fail loudly")
	}
}

func TestUnknownEndpointAnswersInTheErrorEnvelope(t *testing.T) {
	srv, _ := newTestServer(t)
	status, body := request(t, srv, http.MethodGet, "/screne", nil, srv.Token())
	if status != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", status)
	}
	if code := errorField(t, body, "code"); code != "not_found" {
		t.Errorf("code = %q, want not_found", code)
	}
	if hint := errorField(t, body, "hint"); !strings.Contains(hint, "/screen") {
		t.Errorf("hint %q should list the real endpoints", hint)
	}
}

// TestSendKeysBatchIsFullyAppliedBeforeAnswering is the regression for a race
// the old synchronous test harness could not see: tea.Program.Send only queues,
// so answering on "some newer frame" let /keys return with later keys of the
// batch still in flight. A caller that then read /screen saw a mid-sequence
// frame and concluded the key did nothing.
func TestSendKeysBatchIsFullyAppliedBeforeAnswering(t *testing.T) {
	// A per-message delay makes a partially-applied batch the likely outcome
	// if the server does not wait for the whole batch.
	srv, _ := newTestServerWithDelay(t, 5*time.Millisecond)

	status, body := request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"tab", "down", "down", "enter"}}, srv.Token())
	if status != http.StatusOK {
		t.Fatalf("keys: status %d (%v)", status, body)
	}

	// Read immediately — no sleep, no retry. Every key must already be visible.
	_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	text, _ := screen["screen"].(string)
	if strings.Count(text, "key:") != 4 {
		t.Errorf("screen shows %d keys, want 4 — /keys answered before the batch was applied:\n%s",
			strings.Count(text, "key:"), text)
	}
	if !strings.Contains(text, "key:enter") {
		t.Errorf("the last key of the batch is missing:\n%s", text)
	}
}

// TestSendKeysSettlesEvenWhenNothingChanges guards the other side of the same
// protocol: a key the model ignores draws an identical frame, and the request
// must still return promptly rather than burning the settle timeout.
func TestSendKeysSettlesEvenWhenNothingChanges(t *testing.T) {
	srv, _ := newTestServerWithDelay(t, 0)
	// Prime, then send a key the test model renders identically.
	request(t, srv, http.MethodPost, "/keys", map[string]any{"keys": []string{"f5"}}, srv.Token())

	start := time.Now()
	status, _ := request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"f5"}}, srv.Token())
	elapsed := time.Since(start)

	if status != http.StatusOK {
		t.Fatalf("status = %d, want 200", status)
	}
	if elapsed > renderSettleTimeout {
		t.Errorf("took %s — a no-op key should settle on the mark, not wait out the %s timeout",
			elapsed, renderSettleTimeout)
	}
}

func TestWaitRejectsNonPositiveTimeout(t *testing.T) {
	srv, _ := newTestServer(t)
	for _, bad := range []int{0, -1} {
		status, body := request(t, srv, http.MethodPost, "/wait",
			map[string]any{"contains": "x", "timeout_ms": bad}, srv.Token())
		if status != http.StatusBadRequest {
			t.Errorf("timeout_ms=%d: status = %d, want 400", bad, status)
			continue
		}
		if code := errorField(t, body, "code"); code != "bad_timeout" {
			t.Errorf("timeout_ms=%d: code = %q, want bad_timeout", bad, code)
		}
	}

	// Omitting it entirely still means "use the default", not an error.
	status, body := request(t, srv, http.MethodPost, "/wait",
		map[string]any{"contains": "G A I A"}, srv.Token())
	if status != http.StatusOK {
		t.Errorf("omitted timeout_ms: status = %d, want 200 (%v)", status, body)
	}
}

// The hub-era fields are GONE from the wire, not merely unset. A client that
// still reads them has to fail its assertion rather than silently match a zero
// value that means nothing.
func TestTheHubFieldsAreNoLongerServed(t *testing.T) {
	srv, state := newTestServer(t)
	state.setSnapshot(Snapshot{View: ViewChat, Agent: "gaia"})

	_, status := request(t, srv, http.MethodGet, "/status", nil, srv.Token())
	st, _ := status["state"].(map[string]any)
	for _, gone := range []string{
		"hub_tab", "hub_tab_index", "selected_agent_id",
		"visible_agent_ids", "filtering", "can_return_to_hub",
	} {
		if _, present := st[gone]; present {
			t.Errorf("/status still serves %q", gone)
		}
	}

	// And the matchers that read them are refused outright, so a stale client
	// gets a 400 instead of a wait that can never succeed.
	for _, gone := range []string{"hub_tab", "selected_agent_id", "visible_contains", "can_return_to_hub"} {
		code, _ := request(t, srv, http.MethodPost, "/wait",
			map[string]any{"state": map[string]any{gone: "x"}, "timeout_ms": 100}, srv.Token())
		if code != http.StatusBadRequest {
			t.Errorf("/wait on the removed %q matcher = %d, want 400", gone, code)
		}
	}
}

// TestInjectionIsRefusedWhenTheProgramIsNotRunning covers the case that used to
// answer 200 for keys that went nowhere: tea.Program.Send DISCARDS messages once
// the program's context is done, so a TUI the user has quit would happily
// "accept" input and never change.
func TestInjectionIsRefusedWhenTheProgramIsNotRunning(t *testing.T) {
	srv, _ := newTestServer(t)
	srv.MarkStopped() // the user quit; the process has not exited yet

	for _, tc := range []struct {
		path string
		body map[string]any
	}{
		{"/keys", map[string]any{"keys": []string{"enter"}}},
		{"/text", map[string]any{"text": "hello"}},
		{"/resize", map[string]any{"cols": 100, "rows": 30}},
	} {
		status, body := request(t, srv, http.MethodPost, tc.path, tc.body, srv.Token())
		if status != http.StatusServiceUnavailable {
			t.Errorf("%s: status = %d, want 503", tc.path, status)
			continue
		}
		if code := errorField(t, body, "code"); code != "not_running" {
			t.Errorf("%s: code = %q, want not_running", tc.path, code)
		}
		if msg := errorField(t, body, "message"); !strings.Contains(msg, "quit") {
			t.Errorf("%s: message %q should explain why", tc.path, msg)
		}
	}

	// Reads still work — the last frame is exactly what a caller needs to see.
	status, _ := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	if status != http.StatusOK {
		t.Errorf("GET /screen while stopped: status = %d, want 200", status)
	}
}

func TestStatusReportsTheRealRunningState(t *testing.T) {
	srv, _ := newTestServer(t)
	_, body := request(t, srv, http.MethodGet, "/status", nil, srv.Token())
	if body["running"] != true {
		t.Errorf("running = %v while the loop is running, want true", body["running"])
	}

	srv.MarkStopped()
	_, body = request(t, srv, http.MethodGet, "/status", nil, srv.Token())
	if body["running"] != false {
		t.Errorf("running = %v after the loop stopped, want false — it was hardcoded true", body["running"])
	}
}

func TestDelayBudgetIsBounded(t *testing.T) {
	srv, _ := newTestServer(t)
	keys := make([]string, 100)
	for i := range keys {
		keys[i] = "down"
	}
	// 100 keys * 500ms would take 49.5s — far past any client's timeout.
	status, body := request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": keys, "delay_ms": 500}, srv.Token())
	if status != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", status)
	}
	if code := errorField(t, body, "code"); code != "delay_budget_exceeded" {
		t.Errorf("code = %q, want delay_budget_exceeded", code)
	}

	// A single key with any legal delay is fine: the budget counts gaps.
	status, _ = request(t, srv, http.MethodPost, "/keys",
		map[string]any{"keys": []string{"down"}, "delay_ms": 2000}, srv.Token())
	if status != http.StatusOK {
		t.Errorf("one key with delay 2000: status = %d, want 200", status)
	}
}

// TestConcurrentInjectionKeepsBatchesWhole guards the mark protocol under the
// concurrency it will actually see. Each caller sends a batch of keys unique to
// it and, the instant its request returns, checks that its OWN keys are on
// screen. Without serialized injection, caller A's "wait for mark >= 1" is
// satisfied by caller B's later mark 2 while A's keys are still queued — so A
// returns 200 and reads a screen missing its own input.
func TestConcurrentInjectionKeepsBatchesWhole(t *testing.T) {
	srv, _ := newTestServerWithDelay(t, 2*time.Millisecond)

	const callers, perCaller = 4, 3
	var wg sync.WaitGroup
	for i := 1; i <= callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := fmt.Sprintf("f%d", i)
			batch := make([]string, perCaller)
			for j := range batch {
				batch[j] = key
			}

			status, body := request(t, srv, http.MethodPost, "/keys",
				map[string]any{"keys": batch}, srv.Token())
			if status != http.StatusOK {
				t.Errorf("%s: status = %d (%v)", key, status, body)
				return
			}
			if body["settled"] != true {
				t.Errorf("%s: settled = %v, want true", key, body["settled"])
			}

			// No sleep: the 200 is supposed to mean "applied and rendered".
			_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
			text, _ := screen["screen"].(string)
			if got := strings.Count(text, "key:"+key); got < perCaller {
				t.Errorf("%s: only %d/%d of my own keys were on screen when my request returned — another caller's mark satisfied my wait",
					key, got, perCaller)
			}
		}(i)
	}
	wg.Wait()

	_, screen := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	text, _ := screen["screen"].(string)
	if got := strings.Count(text, "key:f"); got != callers*perCaller {
		t.Errorf("screen shows %d function keys, want %d — a batch was lost:\n%s",
			got, callers*perCaller, text)
	}
}

func TestScreenLineCount(t *testing.T) {
	srv, state := newTestServer(t)
	state.recordFrame("one\ntwo\nthree")
	_, body := request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	if body["lines"] != float64(3) {
		t.Errorf("lines = %v, want 3", body["lines"])
	}

	state.recordFrame("")
	_, body = request(t, srv, http.MethodGet, "/screen", nil, srv.Token())
	if body["lines"] != float64(0) {
		t.Errorf("lines = %v for an empty screen, want 0", body["lines"])
	}
}

// Waiting on WHICH row refuses the launch is the point of the blocker matcher:
// the remedy's wording is allowed to change, the row key is not.
func TestWaitAcceptsTheBlockerMatcher(t *testing.T) {
	srv, state := newTestServer(t)
	state.setSnapshot(Snapshot{View: ViewPreflight, Agent: "gaia", Blocker: "binary"})

	status, body := request(t, srv, http.MethodPost, "/wait",
		map[string]any{"state": map[string]any{"blocker": "binary"}, "timeout_ms": 2000}, srv.Token())
	if status != http.StatusOK || body["matched"] != true {
		t.Fatalf("status %d matched %v, want 200/true (%v)", status, body["matched"], body)
	}
	st, _ := body["state"].(map[string]any)
	if st["blocker"] != "binary" {
		t.Errorf("state.blocker = %v, want %q", st["blocker"], "binary")
	}

	// A gate holding on a DIFFERENT row must not match.
	state.setSnapshot(Snapshot{View: ViewPreflight, Agent: "gaia", Blocker: "lemonade"})
	status, body = request(t, srv, http.MethodPost, "/wait",
		map[string]any{"state": map[string]any{"blocker": "binary"}, "timeout_ms": 200}, srv.Token())
	if status != http.StatusRequestTimeout {
		t.Errorf("status = %d, want 408 (%v)", status, body)
	}
}
