package preflight

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/ui/status"
)

// scriptedRunner answers Check with a fixed report and records the fixes asked
// of it. Fix does nothing: these tests drive progress through provisionMsg so
// every frame is deterministic.
type scriptedRunner struct {
	rep   Report
	fixes *[]FixKind
}

func (s scriptedRunner) Label() string                        { return "scripted" }
func (s scriptedRunner) Rows(Config) []Row                    { return append([]Row(nil), s.rep.Rows...) }
func (s scriptedRunner) Check(context.Context, Config) Report { return s.rep }
func (s scriptedRunner) Fix(_ context.Context, _ Config, kind FixKind, _ func(Progress)) FixResult {
	*s.fixes = append(*s.fixes, kind)
	return FixResult{Note: "done"}
}

// firstRunReport is a brand-new machine: the agent is there, nothing else is.
func firstRunReport() Report {
	return Report{AgentID: "gaia", AgentName: "GAIA", Rows: []Row{
		{Key: KeyBinary, Label: "GAIA agent", State: StateOK, Line: `C:\gaia\gaia-agent.exe`},
		{
			Key: KeyLemonade, Label: lemonadeRowLabel, State: StateFailed, FirstRun: true,
			Disposition: status.DispositionHalt, Fix: FixRunSetup,
			Line: "not installed yet", Step: installServerStep,
			Detail: "Lemonade runs AI models on this machine.",
			Remedy: Remedy{Action: "Install the local model server first — it is not on this " +
				"machine.", Command: "gaia init", Where: installDocs},
		},
		{
			Key: KeyModel, Label: modelRowLabel, State: StatePending, Line: "—",
			Step: "download the models (~6 GB)", Detail: `checked once "Lemonade" is fixed`,
		},
	}}
}

func settledOn(t *testing.T, rep Report, w, h int) (Model, *[]FixKind) {
	t.Helper()
	fixes := &[]FixKind{}
	m := newWith(scriptedRunner{rep: rep, fixes: fixes}, Config{AgentID: "gaia", AgentName: "GAIA"}, Options{})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: w, Height: h})
	m = updated.(Model)
	updated, _ = m.Update(reportMsg{rep: rep})
	return updated.(Model), fixes
}

func plain(m Model) string { return ansi.Strip(m.View()) }

func TestFirstRunIsNumberedStepsNotFailures(t *testing.T) {
	m, _ := settledOn(t, firstRunReport(), 100, 24)
	screen := plain(m)
	t.Logf("\n%s", screen)
	assertFits(t, splitLines(screen), 100, 24)

	for _, want := range []string{
		"Step 1 of 2 — install the local model server (~1 min)",
		"Step 2 of 2 — download the models (~6 GB)",
		"enter start step 1",
		"step 1 of 2",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("first-run screen is missing %q", want)
		}
	}
	// Nothing here is broken, so nothing may read as broken.
	if strings.Contains(screen, "[!]") {
		t.Error("a first-run step renders with the failure marker")
	}
	// One route per step: no second command, no link, no contradiction.
	for _, gone := range []string{"gaia init", "https://", "or run:", "Already have it",
		"not on this machine", "f set it up now", "checked once"} {
		if strings.Contains(screen, gone) {
			t.Errorf("first-run screen still offers %q", gone)
		}
	}
}

func TestAFirstRunStepIsNeverPaintedRed(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })

	m, _ := settledOn(t, firstRunReport(), 100, 24)
	red := strings.TrimSuffix(failStyle.Render("X"), "X\x1b[0m")
	if red == "" || red == "X" {
		t.Fatal("could not derive the failure colour; the assertion below would pass vacuously")
	}
	for _, line := range strings.Split(m.View(), "\n") {
		if strings.Contains(ansi.Strip(line), "Lemonade") && strings.Contains(line, red) {
			t.Errorf("the first-run Lemonade row is painted in the failure colour:\n%q", line)
		}
	}

	// And a REAL failure still is.
	rep := firstRunReport()
	rep.Rows[1].FirstRun = false
	m, _ = settledOn(t, rep, 100, 24)
	if !strings.Contains(m.View(), red) {
		t.Error("a real failure lost its colour")
	}
}

func TestEnterStartsTheFocusedStep(t *testing.T) {
	m, fixes := settledOn(t, firstRunReport(), 100, 24)
	if m.FocusKey() != KeyLemonade {
		t.Fatalf("focus = %q, want the first step", m.FocusKey())
	}
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	if !m.Busy() || m.phase != phaseProvisioning {
		t.Fatalf("enter did not start the step (phase %v)", m.phase)
	}
	if cmd == nil {
		t.Fatal("enter started nothing")
	}
	drainUntilDone(t, m)
	if len(*fixes) != 1 || (*fixes)[0] != FixRunSetup {
		t.Errorf("fixes = %v, want one setup run", *fixes)
	}
}

func drainUntilDone(t *testing.T, m Model) {
	t.Helper()
	for ev := range m.provisionCh {
		if ev.done {
			return
		}
	}
}

// #4449: the row kept saying "not running / Press f" while setup ran, and raw
// Python log records scrolled through the status line.
func TestSetupShowsItsProgressOnTheRowAndInWords(t *testing.T) {
	m, _ := settledOn(t, firstRunReport(), 100, 24)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	ch := m.provisionCh
	drainUntilDone(t, m) // the scripted Fix returns at once; progress is fed below
	m.provisionCh = ch

	feed := func(p Progress) {
		updated, _ := m.Update(provisionMsg{ch: ch, event: provisionEvent{progress: p}})
		m = updated.(Model)
	}

	feed(Progress{Row: KeyLemonade, Text: "Downloading the local model server", Percent: 40})
	screen := plain(m)
	t.Logf("\n%s", screen)
	for _, want := range []string{
		"[..]",
		"downloading the local model server…",
		"Step 1 of 2 — downloading the local model server · 40%",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("while installing, the screen is missing %q", want)
		}
	}
	if strings.Contains(screen, "not installed yet") || strings.Contains(screen, "enter start") {
		t.Error("the row still reads as untouched while setup works on it")
	}

	feed(Progress{Row: KeyModel, Text: "Downloading Gemma-4-E4B-it-GGUF", Percent: -1})
	screen = plain(m)
	t.Logf("\n%s", screen)
	lemonadeLine, modelLine := lineWith(screen, "Lemonade"), lineWith(screen, modelRowLabel)
	if !strings.Contains(lemonadeLine, "[ok]") || !strings.Contains(lemonadeLine, "done") {
		t.Errorf("the finished step does not read as done: %q", lemonadeLine)
	}
	if !strings.Contains(modelLine, "[..]") || !strings.Contains(modelLine, "downloading Gemma-4-E4B-it-GGUF") {
		t.Errorf("the model row is not shown in progress: %q", modelLine)
	}
	if !strings.Contains(screen, "Step 2 of 2 — downloading Gemma-4-E4B-it-GGUF") {
		t.Error("the progress line does not name the step it is on")
	}
	if header := strings.SplitN(screen, "\n", 2)[0]; !strings.Contains(header, "step 2 of 2") {
		t.Errorf("the header lags behind the running step: %q", header)
	}
}

// Setup restarts at the server even when the server was already up; that must
// not mark the model row done before its download has begun.
func TestRevisitingAnEarlierRowDoesNotFinishALaterOne(t *testing.T) {
	rep := firstRunReport()
	rep.Rows[1] = Row{Key: KeyLemonade, Label: lemonadeRowLabel, State: StateOK, Line: "running"}
	rep.Rows[2] = Row{Key: KeyModel, Label: modelRowLabel, State: StateFailed, FirstRun: true,
		Disposition: status.DispositionHalt, Fix: FixRunSetup, Line: "not downloaded yet",
		Step: "download the models (~6 GB)"}
	m, _ := settledOn(t, rep, 100, 24)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	ch := m.provisionCh
	drainUntilDone(t, m)
	m.provisionCh = ch

	updated, _ = m.Update(provisionMsg{ch: ch, event: provisionEvent{
		progress: Progress{Row: KeyLemonade, Text: "Starting the local model server", Percent: -1}}})
	m = updated.(Model)
	if modelLine := lineWith(plain(m), modelRowLabel); strings.Contains(modelLine, "done") {
		t.Errorf("the model row reads done before its download began: %q", modelLine)
	}
}

func lineWith(screen, label string) string {
	for _, l := range strings.Split(screen, "\n") {
		if strings.Contains(l, label) {
			return l
		}
	}
	return ""
}

// The local runner's own translation: log records never reach the screen.
func TestSetupOutputIsTranslatedNotEchoed(t *testing.T) {
	cur := Progress{Row: KeyLemonade, Text: "Starting setup", Percent: -1}
	var failure string
	lines := []string{
		"Step 1/5: Starting Lemonade Server...",
		"[2026-09-28 23:28:11] | INFO | gaia.llm.lemonade_embedded._download | lemonade_embedded.py:486 | Downloading https://x",
		"   [========------------] 42% (2.1 MB/5.1 MB)",
		"[2026-09-28 23:28:40] | WARNING | gaia.llm.lemonade_client._post_load_with_transient_retry | x",
		"Step 2/5: Downloading models for 'gaia' profile...",
		"   Downloading: Gemma-4-E4B-it-GGUF",
	}
	var shown []Progress
	for _, l := range lines {
		if next, changed := advance(cur, l, &failure); changed {
			cur = next
			shown = append(shown, cur)
		}
	}
	for _, p := range shown {
		if strings.Contains(p.Text, "|") || strings.Contains(p.Text, ".py") {
			t.Errorf("a log record reached the screen: %q", p.Text)
		}
	}
	want := []Progress{
		{Row: KeyLemonade, Text: "Starting the local model server", Percent: -1},
		{Row: KeyLemonade, Text: "Starting the local model server", Percent: 42},
		{Row: KeyModel, Text: "Downloading the models", Percent: -1},
		{Row: KeyModel, Text: "Downloading Gemma-4-E4B-it-GGUF", Percent: -1},
	}
	if len(shown) != len(want) {
		t.Fatalf("shown = %+v, want %+v", shown, want)
	}
	for i := range want {
		if shown[i] != want[i] {
			t.Errorf("update %d = %+v, want %+v", i, shown[i], want[i])
		}
	}
}

func readyReport() Report {
	return Report{AgentID: "gaia", AgentName: "GAIA", Chat: "Gemma-4-E4B-it-GGUF (3.2 GB, on this machine)",
		Rows: []Row{
			{Key: KeyBinary, Label: "GAIA agent", State: StateOK, Line: "gaia-agent"},
			{Key: KeyLemonade, Label: lemonadeRowLabel, State: StateOK, Line: "running"},
			{Key: KeyModel, Label: modelRowLabel, State: StateOK,
				Line: "Gemma-4-E4B-it-GGUF (3.2 GB) and the embedder load"},
		}}
}

func TestTheReadyScreenNamesTheChatModelAndItsSize(t *testing.T) {
	m, _ := settledOn(t, readyReport(), 100, 24)
	m.fixApplied = true
	screen := oneLine(plain(m))
	t.Logf("\n%s", plain(m))
	for _, want := range []string{
		"Chat runs on Gemma-4-E4B-it-GGUF (3.2 GB, on this machine).",
		"Press enter to start GAIA.",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("ready screen is missing %q", want)
		}
	}

	// Auto-proceeding says it too, on its way out.
	m.fixApplied = false
	m.phase = phaseDone
	if !strings.Contains(plain(m), "chat runs on Gemma-4-E4B-it-GGUF") {
		t.Error("the hand-off line does not name the chat model")
	}
}

// A model that is downloaded and will not load is a real failure: red, named,
// and — for the embedder — a launch the user may still choose.
func TestAModelThatWillNotLoadIsAFailureTheUserSees(t *testing.T) {
	rep := readyReport()
	rep.Chat = ""
	rep.Rows[2] = Row{
		Key: KeyModel, Label: modelRowLabel, State: StateFailed, Optional: true,
		Disposition: status.DispositionHalt,
		Line:        "user.embeddinggemma-300m-GGUF will not load",
		Detail:      "It is downloaded, but the model server could not start it, so document search and memory will not work. Chat still does.",
		Remedy: Remedy{Action: "Stop GAIA's model server, then press r — it starts again with current settings.",
			Command: "gaia lemonade embedded stop", Where: "https://github.com/amd/gaia/issues"},
		Raw: "user.embeddinggemma-300m-GGUF: model_load_error",
	}
	m, _ := settledOn(t, rep, 100, 24)
	screen := oneLine(plain(m))
	t.Logf("\n%s", plain(m))
	for _, want := range []string{
		"[!]", "user.embeddinggemma-300m-GGUF will not load",
		"document search and memory will not work",
		"gaia lemonade embedded stop",
		"Press enter to start without it",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("load-failure screen is missing %q", want)
		}
	}
	if strings.Contains(screen, "checks out") {
		t.Error("a model that will not load was reported as ready")
	}
	if m.phase == phaseDone {
		t.Error("the launch proceeded on its own past a model that will not load")
	}
}

// oneLine joins a wrapped screen back into single-spaced prose for assertions.
func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }

// The provider picker names the step that installs Lemonade, and none once done.
func TestLemonadeStepIsItsFirstRunNumber(t *testing.T) {
	rep := firstRunReport()
	if got := rep.LemonadeStep(); got != 1 {
		t.Errorf("new machine: LemonadeStep() = %d, want 1", got)
	}
	rep.Rows[1] = Row{Key: KeyLemonade, Label: lemonadeRowLabel, State: StateOK}
	if got := rep.LemonadeStep(); got != 0 {
		t.Errorf("Lemonade installed: LemonadeStep() = %d, want 0", got)
	}
}
