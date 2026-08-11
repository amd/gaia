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

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/ui/root"
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
	return filepath.Join(binDir, "gaia-bash"+suffix)
}

// liveTUI boots the real root model inside a real tea.Program, headlessly, with
// the control server attached — the same wiring `gaia tui --control` uses.
type liveTUI struct {
	t     *testing.T
	srv   *control.Server
	prog  *tea.Program
	token string
}

func startLiveTUI(t *testing.T) *liveTUI {
	t.Helper()
	t.Setenv(control.EnvHome, t.TempDir())

	// A fixed catalog and NO hub client, so what these tests drive is decided
	// here and not by whatever daemon and installed agents the machine running
	// them happens to have. --mock is the flag a person would use for the same
	// reason: it makes the one subprocess agent launchable.
	cat := catalog.NewCatalog()
	cat.SetMockBinary(mockBinaryPath(t))
	state := control.NewState(nil)
	prog := tea.NewProgram(
		control.NewRecorder(root.NewRootModelWithHub(cat, nil, false), state),
		tea.WithInput(strings.NewReader("")),
		tea.WithOutput(io.Discard),
		tea.WithoutRenderer(),
	)

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

// TestControlDrivesLiveProgram is the end-to-end proof: a real tea.Program, a
// real root model, driven only over HTTP, with the rendered screen read back.
func TestControlDrivesLiveProgram(t *testing.T) {
	tui := startLiveTUI(t)

	// The hub renders "Loading..." until it knows the terminal size, so the
	// first thing any driver does is set one.
	status, body := tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	if status != http.StatusOK {
		t.Fatalf("POST /resize: status %d (%v)", status, body)
	}
	tui.waitFor(map[string]any{"contains": "Installed"})

	screen := tui.screen()
	if strings.Contains(screen, "Loading...") {
		t.Fatalf("hub is still loading after a resize; screen:\n%s", screen)
	}
	if !strings.Contains(screen, "Bash") {
		t.Fatalf("hub screen does not list the seeded Bash agent; screen:\n%s", screen)
	}
	if strings.Contains(screen, "\x1b[") {
		t.Error("format=plain returned ANSI escape sequences")
	}

	st := tui.state()
	if st["view"] != "hub" {
		t.Errorf("view = %v, want hub", st["view"])
	}
	if st["hub_tab"] != string(catalog.SectionInstalled) {
		t.Errorf("hub_tab = %v, want %q", st["hub_tab"], catalog.SectionInstalled)
	}
	visible, _ := st["visible_agent_ids"].([]any)
	if len(visible) == 0 {
		t.Error("visible_agent_ids is empty; navigation by id would be impossible")
	}
}

// TestControlTabKeyIsTheTabKey is the regression the older smoke tests miss:
// sending tea.KeyRunes{"tab"} types three letters, so the category never
// changes. Driving through the control API must actually switch tabs.
func TestControlTabKeyIsTheTabKey(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": "hub"}})

	before := tui.state()
	tui.keys("tab")
	tui.waitFor(map[string]any{"state": map[string]any{"hub_tab": string(catalog.SectionAvailable)}})

	after := tui.state()
	if before["hub_tab"] == after["hub_tab"] {
		t.Fatalf("hub_tab stayed %v after pressing tab", after["hub_tab"])
	}
	if !strings.Contains(tui.screen(), string(catalog.SectionAvailable)) {
		t.Errorf("screen does not show the Available tab:\n%s", tui.screen())
	}

	tui.keys("shift+tab")
	tui.waitFor(map[string]any{"state": map[string]any{"hub_tab": string(catalog.SectionInstalled)}})
}

// TestControlFilterAndSelect exercises the search flow: "/" opens the filter,
// typed runes narrow it, and the reported selection follows.
func TestControlFilterAndSelect(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": "hub"}})

	tui.keys("/")
	tui.waitFor(map[string]any{"state": map[string]any{"filtering": true}})

	status, body := tui.call(http.MethodPost, "/text", map[string]any{"text": "bash"})
	if status != http.StatusOK {
		t.Fatalf("POST /text: status %d (%v)", status, body)
	}
	tui.waitFor(map[string]any{"contains": "bash"})

	tui.keys("esc")
	tui.waitFor(map[string]any{"state": map[string]any{"filtering": false}})
}

// TestControlHelpOverlay proves a named single-rune key ("?") reaches the model
// and that the overlay is reported as state, not guessed from the screen.
func TestControlHelpOverlay(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": "hub"}})

	tui.keys("?")
	tui.waitFor(map[string]any{"state": map[string]any{"overlay": "help"}})

	tui.keys("esc")
	tui.waitFor(map[string]any{"state": map[string]any{"overlay": ""}})
}

// TestControlResizeChangesLayout covers the narrow-terminal case: the same
// model laid out at 80x24 and at 200x50 must produce different frames.
func TestControlResizeChangesLayout(t *testing.T) {
	tui := startLiveTUI(t)

	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 80, "rows": 24})
	tui.waitFor(map[string]any{"contains": "Installed"})
	narrow := tui.screen()

	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 200, "rows": 50})
	tui.waitFor(map[string]any{"contains": "Installed"})
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
	tui.waitFor(map[string]any{"state": map[string]any{"view": "hub"}})
	tui.keys("tab")
	tui.waitFor(map[string]any{"state": map[string]any{"hub_tab": string(catalog.SectionAvailable)}})

	status, body := tui.call(http.MethodGet, "/frames?since=0&limit=50", nil)
	if status != http.StatusOK {
		t.Fatalf("GET /frames: status %d (%v)", status, body)
	}
	frames, _ := body["frames"].([]any)
	if len(frames) < 2 {
		t.Fatalf("only %d frames recorded; the history is not usable for debugging", len(frames))
	}
	last, _ := frames[len(frames)-1].(map[string]any)
	if screen, _ := last["screen"].(string); !strings.Contains(screen, string(catalog.SectionAvailable)) {
		t.Errorf("the newest frame does not show the Available tab:\n%s", screen)
	}
}

// TestControlWaitTimeoutShowsTheScreen proves a failed wait is debuggable.
func TestControlWaitTimeoutShowsTheScreen(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"contains": "Installed"})

	status, body := tui.call(http.MethodPost, "/wait",
		map[string]any{"contains": "this text is not on any screen", "timeout_ms": 300})
	if status != http.StatusRequestTimeout {
		t.Fatalf("status = %d, want 408 (%v)", status, body)
	}
	envelope, _ := body["error"].(map[string]any)
	screen, _ := envelope["screen"].(string)
	if !strings.Contains(screen, "Installed") {
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

// TestControlReportsHubReturnability covers a destructive mistake a driver
// would otherwise make: in the hub-launched chat esc goes back, but in a
// standalone chat esc QUITS the program. The snapshot has to say which.
func TestControlReportsHubReturnability(t *testing.T) {
	tui := startLiveTUI(t)
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": "hub"}})

	if got := tui.state()["can_return_to_hub"]; got != false {
		t.Errorf("can_return_to_hub = %v in the hub view, want false", got)
	}

	tui.keys("enter") // launch the selected (installed) agent
	tui.waitFor(map[string]any{"state": map[string]any{"view": "chat"}})

	if got := tui.state()["can_return_to_hub"]; got != true {
		t.Errorf("can_return_to_hub = %v in a hub-launched chat, want true — esc returns to the hub here", got)
	}

	tui.keys("esc")
	tui.waitFor(map[string]any{"state": map[string]any{"view": "hub"}})
}
