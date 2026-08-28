package test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"fmt"
	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
	"net/http/httptest"
)

// mockBinaryPath builds the mock agent and returns its path. It must be a real
// agent binary, not this test process: a control test that sends a message
// inside a launched chat would otherwise fork the whole suite.
func mockBinaryPath(t *testing.T) string {
	t.Helper()
	_, binDir := buildBinaries(t)
	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}
	return filepath.Join(binDir, "gaia-agent"+suffix)
}

// liveTUI boots the real root model inside a real tea.Program, headlessly, with
// the control server attached — the same wiring `gaia tui --control` uses.
type liveTUI struct {
	t     *testing.T
	srv   *control.Server
	prog  *tea.Program
	token string
}

// startLiveTUI boots the real launch router headlessly with every host
// dependency stubbed, so what these tests drive is decided here rather than by
// whatever Lemonade, models and daemon the machine running them happens to have.
//
// The three stubs match the local runner's three rows exactly: a mock agent
// binary, a Lemonade that answers /models, and a `gaia init --check` that exits
// 0. Leave any of them out and the gate correctly refuses to reach chat.
func startLiveTUI(t *testing.T) *liveTUI {
	t.Helper()
	t.Setenv(control.EnvHome, t.TempDir())
	isolateGaiaHome(t)
	stubSetupCheck(t, 0)
	t.Setenv("LEMONADE_BASE_URL", stubLemonade(t))

	// SetMockBinary, not a hand-edited entry: it owns the invariant that a
	// mock replaces the whole how-to-talk-to-this-binary set, CanonicalEvents
	// included.
	cat := catalog.NewCatalog()
	cat.SetMockBinary(mockBinaryPath(t))
	agent := cat.Get(catalog.FlagshipID)
	if agent == nil {
		t.Fatalf("the catalog has no %q entry", catalog.FlagshipID)
	}

	state := control.NewState(nil)
	model := root.NewFlagshipModel(*agent, false).
		WithLocalPreflight(preflight.LocalOptions{Binary: agent.BinaryPath}).
		WithPreflight(nil, preflight.Options{ReadyHold: time.Millisecond})
	return runLiveTUI(t, tea.NewProgram(
		control.NewRecorder(model, state),
		tea.WithInput(strings.NewReader("")),
		tea.WithOutput(io.Discard),
		tea.WithoutRenderer(),
	), state, model)
}

// runLiveTUI starts the program and the control server around it, and tears
// both down when the test ends.
func runLiveTUI(t *testing.T, prog *tea.Program, state *control.State, closer io.Closer) *liveTUI {
	t.Helper()
	srv, err := control.Start(prog, state, control.Options{Version: "test"})
	if err != nil {
		t.Fatalf("control.Start: %v", err)
	}

	// Bracket Run exactly as ui.run does: the control server refuses injection
	// while the event loop is not consuming messages.
	srv.MarkRunning()
	done := make(chan error, 1)
	go func() {
		_, runErr := prog.Run()
		srv.MarkStopped()
		done <- runErr
	}()

	t.Cleanup(func() {
		prog.Quit()
		// ui.run closes the model once the loop stops, and so must this: the
		// agent is a child process that Windows will not let the temp dir be
		// removed while it lives.
		defer closer.Close()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("program exited with %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Error("program did not exit within 5s")
		}
		if err := srv.Stop(); err != nil {
			t.Errorf("control.Stop: %v", err)
		}
	})

	return &liveTUI{t: t, srv: srv, prog: prog, token: srv.Token()}
}

func (l *liveTUI) call(method, path string, body any) (int, map[string]any) {
	l.t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			l.t.Fatalf("marshal: %v", err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, l.srv.BaseURL()+"/control/v1"+path, reader)
	if err != nil {
		l.t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer "+l.token)
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		l.t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	var decoded map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&decoded); err != nil {
		l.t.Fatalf("%s %s: decode: %v", method, path, err)
	}
	return resp.StatusCode, decoded
}

func (l *liveTUI) screen() string {
	l.t.Helper()
	status, body := l.call(http.MethodGet, "/screen", nil)
	if status != http.StatusOK {
		l.t.Fatalf("GET /screen: status %d (%v)", status, body)
	}
	text, _ := body["screen"].(string)
	return text
}

func (l *liveTUI) state() map[string]any {
	l.t.Helper()
	status, body := l.call(http.MethodGet, "/status", nil)
	if status != http.StatusOK {
		l.t.Fatalf("GET /status: status %d (%v)", status, body)
	}
	st, _ := body["state"].(map[string]any)
	return st
}

// frameContaining returns the first recorded frame holding text, or "" when
// none did. A screen that is on for one render can only be proven from here.
func (l *liveTUI) frameContaining(text string) string {
	l.t.Helper()
	status, body := l.call(http.MethodGet, "/frames?since=0&limit=200", nil)
	if status != http.StatusOK {
		l.t.Fatalf("GET /frames: status %d (%v)", status, body)
	}
	frames, _ := body["frames"].([]any)
	for _, f := range frames {
		frame, _ := f.(map[string]any)
		if screen, _ := frame["screen"].(string); strings.Contains(screen, text) {
			return screen
		}
	}
	return ""
}

func (l *liveTUI) waitFor(matcher map[string]any) map[string]any {
	l.t.Helper()
	if _, ok := matcher["timeout_ms"]; !ok {
		matcher["timeout_ms"] = 5000
	}
	status, body := l.call(http.MethodPost, "/wait", matcher)
	if status != http.StatusOK {
		envelope, _ := body["error"].(map[string]any)
		l.t.Fatalf("POST /wait %v: status %d\nmessage: %v\nscreen was:\n%v",
			matcher, status, envelope["message"], envelope["screen"])
	}
	return body
}

func (l *liveTUI) keys(names ...string) {
	l.t.Helper()
	status, body := l.call(http.MethodPost, "/keys", map[string]any{"keys": names})
	if status != http.StatusOK {
		l.t.Fatalf("POST /keys %v: status %d (%v)", names, status, body)
	}
}

// TestFlagshipBootReachesChat is the end-to-end proof: a real tea.Program, the
// real launch router, driven only over HTTP, with the rendered screen read
// back. It walks the whole boot — splash, readiness, chat — and then holds a
// turn with the mock agent.
func TestFlagshipBootReachesChat(t *testing.T) {
	tui := startLiveTUI(t)

	// Resize FIRST: cols and rows are 0 until the program is told, and every
	// layout below wraps wrongly against that.
	status, body := tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	if status != http.StatusOK {
		t.Fatalf("POST /resize: status %d (%v)", status, body)
	}

	// 1. Splash, 2. the readiness gate, 3. chat — with nothing to press in
	// between. The walk is read back from the FRAME HISTORY rather than waited
	// on view by view: the splash holds for a single render, so a /wait issued
	// after the fact races it and times out having missed nothing.
	tui.waitFor(map[string]any{
		"state": map[string]any{"view": control.ViewChat}, "timeout_ms": 20000})

	splash := tui.frameContaining("G A I A")
	if splash == "" {
		t.Error("no recorded frame carried the wordmark; the launch never showed a splash")
	} else if !strings.Contains(splash, "+#############*=") {
		t.Errorf("the splash frame does not carry the mascot:\n%s", splash)
	}
	if tui.frameContaining("Getting GAIA ready") == "" {
		t.Error("no recorded frame showed the readiness gate; the launch skipped it")
	}

	chatScreen := tui.screen()
	if !strings.Contains(chatScreen, "Welcome to GAIA") {
		t.Errorf("the chat view never rendered its welcome:\n%s", chatScreen)
	}
	if !strings.Contains(chatScreen, "Ask anything") {
		t.Errorf("the composer is not on screen:\n%s", chatScreen)
	}
	// Nothing about the hub may have survived on any frame.
	for _, gone := range []string{"Installed (", "Available (", "Coming Soon", "i install"} {
		if strings.Contains(chatScreen, gone) {
			t.Errorf("the chat screen still says %q:\n%s", gone, chatScreen)
		}
	}

	// 4. A real turn against the mock agent. The wait is on the ANSWER landing,
	// not on a sleep — it returns the instant the stream settles.
	status, body = tui.call(http.MethodPost, "/text", map[string]any{"text": "list my files"})
	if status != http.StatusOK {
		t.Fatalf("POST /text: status %d (%v)", status, body)
	}
	tui.keys("enter")
	tui.waitFor(map[string]any{"contains": "Summary:", "timeout_ms": 20000})
	tui.waitFor(map[string]any{"state": map[string]any{"streaming": false}, "timeout_ms": 20000})

	answered := tui.screen()
	if !strings.Contains(answered, "list my files") {
		t.Errorf("the query never reached the transcript:\n%s", answered)
	}
}

// The other half of the gate: with the agent binary gone it must settle on
// preflight and STAY there. A launch that reaches chat anyway is the exact
// failure the gate exists to prevent.
func TestFlagshipWithNoAgentBinaryNeverReachesChat(t *testing.T) {
	tui := startLiveTUIMissingAgent(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})

	// Asserted on model state, not on rendered text.
	tui.waitFor(map[string]any{"state": map[string]any{
		"view": control.ViewPreflight, "blocker": preflight.KeyBinary}})

	screen := tui.screen()
	if !strings.Contains(screen, catalog.InstallerURL) {
		t.Errorf("the halt screen does not name the installer:\n%s", screen)
	}

	// And it must not become chat while nobody is looking. A /wait that TIMES
	// OUT is the assertion here.
	status, _ := tui.call(http.MethodPost, "/wait",
		map[string]any{"state": map[string]any{"view": control.ViewChat}, "timeout_ms": 2000})
	if status != http.StatusRequestTimeout {
		t.Fatalf("the launch reached chat with no agent binary (wait status %d)\n%s", status, tui.screen())
	}
}

// TestControlHelpOverlay proves a named single-rune key reaches the model and
// that the overlay is reported as state, not guessed from the screen.
func TestControlHelpOverlay(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}})

	tui.call(http.MethodPost, "/text", map[string]any{"text": "/help"})
	tui.keys("enter")
	tui.waitFor(map[string]any{"state": map[string]any{"overlay": "help"}})

	tui.keys("esc")
	tui.waitFor(map[string]any{"state": map[string]any{"overlay": ""}})
}

// TestControlResizeChangesLayout covers the narrow-terminal case: the same
// model laid out at 80x24 and at 200x50 must produce different frames.
func TestControlResizeChangesLayout(t *testing.T) {
	tui := startLiveTUI(t)

	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 80, "rows": 24})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}})
	narrow := tui.screen()

	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 200, "rows": 50})
	tui.waitFor(map[string]any{"contains": "Welcome to GAIA"})
	wide := tui.screen()

	if narrow == wide {
		t.Error("the screen is identical at 80x24 and 200x50; the resize never reached the layout")
	}
	if widest(narrow) > 80 {
		t.Errorf("a line of %d columns overflows the 80-column terminal", widest(narrow))
	}
}

// TestControlFramesRecordHistory checks the debugging endpoint returns the
// frames that led to the current screen.
func TestControlFramesRecordHistory(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}})

	status, body := tui.call(http.MethodGet, "/frames?since=0&limit=50", nil)
	if status != http.StatusOK {
		t.Fatalf("GET /frames: status %d (%v)", status, body)
	}
	frames, _ := body["frames"].([]any)
	if len(frames) < 2 {
		t.Fatalf("only %d frames recorded; the history is not usable for debugging", len(frames))
	}
	// The splash really was rendered, not skipped past — the frame history is
	// where that is provable after the fact.
	var sawSplash bool
	for _, f := range frames {
		frame, _ := f.(map[string]any)
		if screen, _ := frame["screen"].(string); strings.Contains(screen, "G A I A") {
			sawSplash = true
			break
		}
	}
	if !sawSplash {
		t.Error("no recorded frame shows the splash; the launch skipped straight past it")
	}
}

// TestControlWaitTimeoutShowsTheScreen proves a failed wait is debuggable.
func TestControlWaitTimeoutShowsTheScreen(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"contains": "Welcome to GAIA"})

	status, body := tui.call(http.MethodPost, "/wait",
		map[string]any{"contains": "this text is not on any screen", "timeout_ms": 300})
	if status != http.StatusRequestTimeout {
		t.Fatalf("status = %d, want 408 (%v)", status, body)
	}
	envelope, _ := body["error"].(map[string]any)
	screen, _ := envelope["screen"].(string)
	if !strings.Contains(screen, "Welcome to GAIA") {
		t.Errorf("the timeout did not report the real screen; got:\n%s", screen)
	}
}

func widest(screen string) int {
	max := 0
	for _, line := range strings.Split(screen, "\n") {
		if n := len([]rune(line)); n > max {
			max = n
		}
	}
	return max
}

// startLiveTUIMissingAgent is startLiveTUI with the one stub the gate is meant
// to catch left out.
func startLiveTUIMissingAgent(t *testing.T) *liveTUI {
	t.Helper()
	t.Setenv(control.EnvHome, t.TempDir())
	isolateGaiaHome(t)

	agent := catalog.NewCatalog().Get(catalog.FlagshipID)
	agent.BinaryPath = "gaia-agent-that-was-never-installed"

	state := control.NewState(nil)
	model := root.NewFlagshipModel(*agent, false).
		WithLocalPreflight(preflight.LocalOptions{Binary: agent.BinaryPath}).
		WithPreflight(nil, preflight.Options{ReadyHold: time.Millisecond})
	return runLiveTUI(t, tea.NewProgram(
		control.NewRecorder(model, state),
		tea.WithInput(strings.NewReader("")),
		tea.WithOutput(io.Discard),
		tea.WithoutRenderer(),
	), state, model)
}

// stubLemonade stands in for the local model server: it answers /models and
// nothing else, which is exactly what the Local AI row probes.
func stubLemonade(t *testing.T) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/models") {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"data":[]}`)
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/api/v1"
}
