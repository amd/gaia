package test

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/gaiaprogram"
	"github.com/amd/gaia/tui/internal/ui/preflight"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// nativeAgentEnv optionally names a real gaiacpp for this test to switch to;
// unset, a copy of the mock agent stands in for it.
const nativeAgentEnv = "GAIA_TEST_GAIACPP"

// shotsEnv optionally names a directory to save each stage's screen into, as
// text and as an SVG picture.
const shotsEnv = "GAIA_AGENT_CHOICE_SHOTS"

func copyProgram(t *testing.T, src, dst string) {
	t.Helper()
	in, err := os.Open(src)
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		t.Fatal(err)
	}
	if err := out.Close(); err != nil {
		t.Fatal(err)
	}
}

// shot saves the screen for one stage when shotsEnv asks for it.
func (l *liveTUI) shot(name string) {
	l.t.Helper()
	dir := os.Getenv(shotsEnv)
	if dir == "" {
		return
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		l.t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name+".txt"), []byte(l.screen()), 0o644); err != nil {
		l.t.Fatal(err)
	}
	req, err := http.NewRequest(http.MethodGet, l.srv.BaseURL()+"/control/v1/screen?format=svg", nil)
	if err != nil {
		l.t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+l.token)
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		l.t.Fatal(err)
	}
	defer resp.Body.Close()
	svg, err := io.ReadAll(resp.Body)
	if err != nil || resp.StatusCode != http.StatusOK {
		l.t.Fatalf("GET /screen?format=svg: status %d, %v", resp.StatusCode, err)
	}
	if err := os.WriteFile(filepath.Join(dir, name+".svg"), svg, 0o644); err != nil {
		l.t.Fatal(err)
	}
}

func (l *liveTUI) run(command string) {
	l.t.Helper()
	status, body := l.call(http.MethodPost, "/text", map[string]any{"text": command})
	if status != http.StatusOK {
		l.t.Fatalf("POST /text %q: status %d (%v)", command, status, body)
	}
	// Esc closes the palette, so Enter submits exactly what was typed.
	l.keys("esc", "enter")
}

// /agent, driven over the control API against a real program: the listing,
// a switch to gaiacpp, a switch back, and gaiacpp gone from the machine and
// downloaded again.
func TestAgentProgramSwitchEndToEnd(t *testing.T) {
	suffix := ""
	if runtime.GOOS == "windows" {
		suffix = ".exe"
	}
	mock := mockBinaryPath(t)
	programs := t.TempDir()
	copyProgram(t, mock, filepath.Join(programs, catalog.PythonAgentBinary+suffix))
	nativePath := filepath.Join(programs, catalog.NativeAgentBinary+suffix)
	nativeSource := os.Getenv(nativeAgentEnv)
	if nativeSource == "" {
		nativeSource = mock
	}
	copyProgram(t, nativeSource, nativePath)

	// The stand-in for the release: counted, so a declined offer can be checked.
	var downloads atomic.Int32

	// gaiacpp's gate asks the model server whether its default model is on disk.
	lemonadeStub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/models") {
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"data":[{"id":"Gemma-4-E4B-it-GGUF","downloaded":true,"labels":["chat"]}]}`)
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(lemonadeStub.Close)

	// A program found by name is run as the real flagship, which speaks the
	// canonical protocol and warms up; the stand-ins have to as well.
	t.Setenv("MOCKAGENT_CANONICAL", "1")
	tui := startLiveTUIWith(t, func(root.FlagshipModel) root.FlagshipModel {
		// Set here, after the harness has built its binaries and isolated the
		// home: only these two programs are findable, whatever is installed
		// on the machine running the test.
		path := programs
		if runtime.GOOS == "windows" {
			path += string(os.PathListSeparator) + filepath.Join(os.Getenv("SystemRoot"), "System32")
		} else {
			path += string(os.PathListSeparator) + "/usr/bin" + string(os.PathListSeparator) + "/bin"
		}
		t.Setenv("PATH", path)
		t.Chdir(t.TempDir())
		t.Setenv("LEMONADE_BASE_URL", lemonadeStub.URL+"/api/v1")
		t.Setenv("GAIA_CONFIG_FILE", filepath.Join(t.TempDir(), "config.json"))

		// The launch goes through the real selection, as gaia-cli does when
		// the Python agent is asked for.
		cat, _, err := catalog.ForFlagship(catalog.PythonAgentBinary)
		if err != nil {
			t.Fatalf("selecting the flagship's program: %v", err)
		}
		agent := cat.Get(catalog.FlagshipID)
		return root.NewFlagshipModel(*agent, false).WithCatalog(cat).
			WithLocalPreflight(preflight.LocalOptions{Binary: agent.BinaryPath}).
			WithPreflight(nil, preflight.Options{ReadyHold: time.Millisecond}).
			WithProgramFetch(
				func(context.Context) (gaiaprogram.Status, error) {
					return gaiaprogram.Status{CanDownload: true, SizeBytes: 12 << 20, Version: "0.27.0", Target: nativePath}, nil
				},
				func(_ context.Context, progress func(gaiaprogram.Progress)) (string, error) {
					downloads.Add(1)
					progress(gaiaprogram.Progress{Done: 12 << 20, Total: 12 << 20})
					copyProgram(t, nativeSource, nativePath)
					return nativePath, nil
				})
	})

	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 46})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}})
	tui.waitFor(map[string]any{"contains": "Ask anything"})

	// 1. The listing: both programs, Beta, which is running.
	tui.run("/agent")
	tui.waitFor(map[string]any{"contains": "Agent programs"})
	listing := tui.screen()
	for _, want := range []string{"gaiapy — Python agent", "running now", "gaiacpp — Native C++ agent · Beta",
		"Mature and well tested", "Fast and light", "No saved choice yet"} {
		if !strings.Contains(listing, want) {
			t.Errorf("the listing does not say %q:\n%s", want, listing)
		}
	}
	if strings.Contains(listing, "Unavailable") {
		t.Errorf("an installed program is listed as unavailable:\n%s", listing)
	}
	tui.shot("1-agent-listing")

	// The palette is the picker: the space after /agent lists both programs.
	tui.call(http.MethodPost, "/text", map[string]any{"text": "/agent "})
	tui.waitFor(map[string]any{"contains": "/agent gaiacpp"})
	tui.shot("2-agent-picker")
	tui.keys("esc", "esc")

	// 2. Switch to gaiacpp: through the gate, back in chat, header marked.
	tui.run("/agent gaiacpp")
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat},
		"contains": "(" + catalog.NativeAgentBinary + " · Beta)"})
	// The new agent warms up behind a stage that covers the transcript, and a
	// command typed meanwhile runs once it is ready: its answer is the wait.
	tui.run("/agent")
	tui.waitFor(map[string]any{"contains": "Beta · running now · your saved choice"})
	onNative := tui.screen()
	for _, want := range []string{"Agent programs", "Switched the agent program from gaiapy to gaiacpp",
		"gaiacpp now answers", "agent gaia"} {
		if !strings.Contains(onNative, want) {
			t.Errorf("after the switch the screen does not say %q:\n%s", want, onNative)
		}
	}
	if saved, err := preflight.ReadFlagshipAgent(); err != nil || saved.Choice != preflight.FlagshipAgentNative {
		t.Errorf("the switch saved %+v, %v", saved, err)
	}
	tui.shot("3-switched-to-gaiacpp")

	// 3. And back.
	tui.run("/agent gaiapy")
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat},
		"absent": "(" + catalog.NativeAgentBinary + " · Beta)"})
	tui.run("/agent")
	tui.waitFor(map[string]any{"contains": "Python agent (gaia-agent) · running now · your saved choice"})
	onPython := tui.screen()
	if !strings.Contains(onPython, "Switched the agent program from gaiacpp to gaiapy") {
		t.Errorf("no divider for the switch back:\n%s", onPython)
	}
	if strings.Contains(strings.SplitN(onPython, "\n", 2)[0], catalog.NativeAgentBinary) {
		t.Errorf("the header still marks gaiacpp after switching back:\n%s", onPython)
	}
	if saved, err := preflight.ReadFlagshipAgent(); err != nil || saved.Choice != preflight.FlagshipAgentPython {
		t.Errorf("the switch back saved %+v, %v", saved, err)
	}
	tui.shot("4-switched-back-to-gaiapy")

	// 4. gaiacpp gone: listed as unavailable with where it was looked for, and
	// a switch to it refused with nothing changed.
	removed := false
	for deadline := time.Now().Add(15 * time.Second); time.Now().Before(deadline); time.Sleep(200 * time.Millisecond) {
		if err := os.Remove(nativePath); err == nil || os.IsNotExist(err) {
			removed = true
			break
		}
	}
	if !removed {
		t.Fatalf("could not remove %s: the retired agent still holds it", nativePath)
	}
	tui.run("/agent")
	tui.waitFor(map[string]any{"contains": "Not on this PC yet: /agent gaiacpp offers to download it."})
	tui.shot("5-gaiacpp-unavailable")

	// 6. Asking for it offers the download; Esc leaves everything as it was.
	tui.run("/agent gaiacpp")
	tui.waitFor(map[string]any{"contains": "Press Enter to download it and switch, or Esc to cancel"})
	offer := tui.screen()
	for _, want := range []string{"gaiacpp (Beta) is not on this PC yet", "12.0 MB", "checksums"} {
		if !strings.Contains(offer, want) {
			t.Errorf("the offer does not say %q:\n%s", want, offer)
		}
	}
	tui.shot("6-gaiacpp-download-offered")
	tui.keys("esc")
	tui.waitFor(map[string]any{"contains": "gaiacpp was not downloaded"})
	_, status := tui.call(http.MethodGet, "/status", nil)
	if state, _ := status["state"].(map[string]any); state["view"] != control.ViewChat {
		t.Errorf("a declined download left the chat: %v", state["view"])
	}
	if saved, err := preflight.ReadFlagshipAgent(); err != nil || saved.Choice != preflight.FlagshipAgentPython {
		t.Errorf("a declined download changed the saved choice: %+v, %v", saved, err)
	}
	if n := downloads.Load(); n != 0 {
		t.Fatalf("a declined offer downloaded gaiacpp %d time(s)", n)
	}

	// 7. Enter downloads it, and the session moves to gaiacpp.
	tui.run("/agent gaiacpp")
	// The first offer's text is still on screen, so the download itself is
	// what says the second one was reached; Enter before then does nothing.
	for i := 0; downloads.Load() == 0 && i < 50; i++ {
		tui.keys("enter")
		time.Sleep(100 * time.Millisecond)
	}
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}, "contains": "(gaiacpp · Beta)", "timeout_ms": 20000})
	// The transcript comes back once the new agent has warmed up.
	tui.waitFor(map[string]any{"contains": "Downloaded gaiacpp 0.27.0", "timeout_ms": 20000})
	if n := downloads.Load(); n != 1 {
		t.Fatalf("gaiacpp was downloaded %d times, want once", n)
	}
	downloaded := tui.screen()
	for _, want := range []string{"Downloaded gaiacpp 0.27.0 and checked it against the release's checksum", "from gaiapy to gaiacpp"} {
		if !strings.Contains(downloaded, want) {
			t.Errorf("the screen does not say %q:\n%s", want, downloaded)
		}
	}
	if saved, err := preflight.ReadFlagshipAgent(); err != nil || saved.Choice != preflight.FlagshipAgentNative {
		t.Errorf("the downloaded gaiacpp was not saved as the choice: %+v, %v", saved, err)
	}
	tui.shot("7-gaiacpp-downloaded-and-running")
}
