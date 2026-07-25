package preflight

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
)

// settle applies a fresh Check result the way Init's command would.
func settle(t *testing.T, m Model, f *fakeTransport) Model {
	t.Helper()
	updated, _ := m.Update(reportMsg{rep: Check(t.Context(), f, EmailConfig())})
	return updated.(Model)
}

func newModel(t *testing.T, f *fakeTransport) Model {
	t.Helper()
	m := New(f, EmailConfig(), Options{ManualProceed: true, ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	return settle(t, updated.(Model), f)
}

func TestFixStartsTheDaemonThenRechecks(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}

	m := newModel(t, f)
	if m.FocusKey() != KeyDaemon {
		t.Fatalf("focus = %q, want the daemon row", m.FocusKey())
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
	m = updated.(Model)
	if !m.Busy() {
		t.Error("pressing f did not show the screen as busy")
	}
	if cmd == nil {
		t.Fatal("pressing f produced no command")
	}

	// The fix succeeded (the fake clears its attach error), so the screen must
	// re-check rather than claim success on its own.
	// Run the real fix command, so the fake actually performs the start.
	updated, cmd = m.Update(m.startDaemonCmd()())
	m = updated.(Model)
	if !m.Busy() {
		t.Error("a successful fix did not trigger a re-check")
	}
	if !strings.Contains(ansi.Strip(m.View()), "Re-checking") {
		t.Errorf("the screen does not say it is re-checking:\n%s", m.View())
	}
	_ = cmd

	m = settle(t, m, f)
	if row, _ := m.Report().Find(KeyDaemon); row.State != StateOK {
		t.Errorf("after the fix the daemon row is %s:\n%s", row.State.Word(), m.Report())
	}
}

func TestAFailedFixExplainsItselfInsteadOfSilentlyRetrying(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
	m := newModel(t, f)

	updated, _ := m.Update(fixDoneMsg{
		key: KeyDaemon,
		err: &daemon.StartError{Reason: "the `gaia` CLI is not on PATH"},
	})
	m = updated.(Model)

	// Flattened: the note wraps, so an assertion on raw lines would be about
	// where the wrap fell rather than about what the screen says.
	screen := strings.Join(strings.Fields(ansi.Strip(m.View())), " ")
	if m.Busy() {
		t.Error("a failed fix left the screen spinning")
	}
	for _, want := range []string{"Fix failed", "not on PATH", "gaia daemon start"} {
		if !strings.Contains(screen, want) {
			t.Errorf("screen does not show %q:\n%s", want, screen)
		}
	}
}

func TestFixDownloadsTheModelAndUsesTheFinalLine(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)
	f.streamBody = "→ Pulling Gemma-4-E4B-it-GGUF…\n✓ Provisioning complete.\n"

	m := newModel(t, f)
	if m.FocusKey() != KeyModel {
		t.Fatalf("focus = %q, want the model row", m.FocusKey())
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
	m = updated.(Model)
	if cmd == nil {
		t.Fatal("pressing f on a missing model produced no command")
	}
	if !strings.Contains(ansi.Strip(m.View()), "Leaving this screen cancels the download") {
		t.Errorf("the download state does not tell the user what leaving does:\n%s", m.View())
	}

	// Drain the provisioning channel the way the runtime would.
	ch := m.provisionCh
	if ch == nil {
		t.Fatal("no provisioning channel was opened")
	}
	deadline := time.After(5 * time.Second)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				t.Fatal("the provisioning channel closed without a terminal event")
			}
			updated, _ := m.Update(provisionMsg{ch: ch, event: ev})
			m = updated.(Model)
			if ev.done {
				if !ev.result.OK {
					t.Fatalf("provisioning failed: %+v", ev.result)
				}
				if !m.Busy() {
					t.Error("a successful download did not trigger a re-check")
				}
				return
			}
		case <-deadline:
			t.Fatal("provisioning never produced a terminal event")
		}
	}
}

func TestALateProvisionEventCannotDriveTheScreen(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)
	m := newModel(t, f)

	stale := make(chan provisionEvent, 1)
	updated, _ := m.Update(provisionMsg{ch: stale, event: provisionEvent{
		done:   true,
		result: ProvisionResult{OK: true},
	}})
	m = updated.(Model)

	if m.Busy() {
		t.Error("an event from a cancelled download restarted the checks")
	}
	if row, _ := m.Report().Find(KeyModel); row.State != StateFailed {
		t.Errorf("a stale event changed the report: model row is %s", row.State.Word())
	}
}

func TestRecheckResetsTheScreen(t *testing.T) {
	f := newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
	m := newModel(t, f)

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}})
	m = updated.(Model)
	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'r'}})
	m = updated.(Model)

	if cmd == nil {
		t.Fatal("r produced no command")
	}
	if !m.Busy() {
		t.Error("r did not put the screen back into checking")
	}
	if m.details {
		t.Error("r left the details pane open over a screen that is being re-read")
	}
	if !strings.Contains(ansi.Strip(m.View()), "checking") {
		t.Errorf("the re-checking screen does not say so:\n%s", m.View())
	}
}

func TestCancelStopsInFlightWork(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)
	// A body that never completes would block the goroutine; an empty one is
	// enough to prove Cancel tears the channel reference down.
	m := newModel(t, f)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
	m = updated.(Model)

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(Model)
	if m.provisionCh != nil {
		t.Error("esc left the provisioning channel attached")
	}
	if _, ok := cmd().(CancelMsg); !ok {
		t.Fatalf("esc produced %T, want CancelMsg", cmd())
	}
}

func TestCtrlCQuitsTheApp(t *testing.T) {
	m := newModel(t, newFake())
	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	if cmd == nil {
		t.Fatal("ctrl+c produced no command")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatalf("ctrl+c produced %T, want tea.QuitMsg", cmd())
	}
}
