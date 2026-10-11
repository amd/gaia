package preflight

import (
	"context"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
)

// After a reboot the daemon and the agent are both simply not running. The gate
// starts them itself, so the launch needs no keypress at all.

func newAutoModel(t *testing.T, f *fakeTransport) (Model, tea.Cmd) {
	t.Helper()
	m := New(f, EmailConfig(), Options{ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	updated, cmd := updated.(Model).Update(reportMsg{rep: Check(t.Context(), f, EmailConfig())})
	return updated.(Model), cmd
}

// runAutoFix runs the fix half of the batch autoStart returned and feeds its
// answer back, the way the runtime would.
func runAutoFix(t *testing.T, m Model, cmd tea.Cmd) (Model, tea.Cmd) {
	t.Helper()
	if cmd == nil {
		t.Fatal("no command was scheduled")
	}
	batch, ok := cmd().(tea.BatchMsg)
	if !ok {
		t.Fatal("the auto-start did not schedule a batch")
	}
	for _, c := range batch {
		if c == nil {
			continue
		}
		if done, ok := c().(fixDoneMsg); ok {
			updated, next := m.Update(done)
			return updated.(Model), next
		}
	}
	t.Fatal("the batch carried no fix")
	return m, nil
}

func TestADownDaemonIsStartedWithoutAKeypress(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}

	m, cmd := newAutoModel(t, f)
	if !m.Busy() {
		t.Fatal("the gate waited for a keypress over a daemon it may start itself")
	}
	row, _ := m.Report().Find(KeyDaemon)
	if row.State != StateChecking || row.Fix != FixNone {
		t.Errorf("the row being started reads %s with fix %v, want checking with no key", row.State.Word(), row.Fix)
	}
	screen := ansi.Strip(m.View())
	for _, want := range []string{"starting…", "Starting the background service"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the screen does not show %q:\n%s", want, screen)
		}
	}

	m, _ = runAutoFix(t, m, cmd)
	if m.phase != phaseChecking {
		t.Errorf("a successful start did not re-check (phase %d)", m.phase)
	}
	if m.fixApplied {
		t.Error("an automatic start holds the screen for enter, which is the keypress it exists to save")
	}
	m = settle(t, m, f)
	if row, _ := m.Report().Find(KeyDaemon); row.State != StateOK {
		t.Errorf("after the start the daemon row is %s", row.State.Word())
	}
}

func TestAStoppedAgentIsStartedWithoutAKeypress(t *testing.T) {
	f := newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
	ensured := 0
	f.ensureFn = func(context.Context) error {
		ensured++
		f.with("GET /daemon/v1/agents", 200, agentsRunning)
		return nil
	}

	m, cmd := newAutoModel(t, f)
	if m.FocusKey() != KeySidecar || !m.Busy() {
		t.Fatalf("the stopped agent was not started (focus %q, busy %v)", m.FocusKey(), m.Busy())
	}
	if !strings.Contains(ansi.Strip(m.View()), "Starting the Email agent") {
		t.Errorf("the screen does not say what it is starting:\n%s", m.View())
	}
	m, _ = runAutoFix(t, m, cmd)
	if ensured != 1 {
		t.Fatalf("the agent was ensured %d times, want 1", ensured)
	}
	m = settle(t, m, f)
	if !m.Report().Ready() {
		t.Errorf("after the start the report is not ready:\n%s", m.Report())
	}
}

func TestAFailedAutoStartGivesTheRowBackWithItsKey(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
	f.startErr = &daemon.StartError{Reason: "the `gaia` CLI is not on PATH"}

	m, cmd := newAutoModel(t, f)
	m, next := runAutoFix(t, m, cmd)
	if next != nil || m.Busy() {
		t.Fatal("a failed start left the screen busy or scheduled more work")
	}
	row, _ := m.Report().Find(KeyDaemon)
	if row.State != StateFailed || row.Fix != FixStartDaemon {
		t.Fatalf("the row did not come back failed with its key: %s, fix %v", row.State.Word(), row.Fix)
	}
	screen := strings.Join(strings.Fields(ansi.Strip(m.View())), " ")
	for _, want := range []string{"Could not start the background service automatically", "not on PATH", "press f to try again", "f start it for me"} {
		if !strings.Contains(screen, want) {
			t.Errorf("the screen does not show %q:\n%s", want, screen)
		}
	}

	// A re-check that finds it still down reports it; it does not start again.
	updated, cmd := m.Update(reportMsg{rep: Check(t.Context(), f, EmailConfig())})
	m = updated.(Model)
	if m.Busy() || cmd != nil {
		t.Error("a start that already failed was attempted a second time")
	}
}

func TestAForeignDaemonRecordIsNotReclaimedAutomatically(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.StaleError{Kind: daemon.StaleForeign, Path: "/tmp/i.json", Reason: "served by something else"}

	m, cmd := newAutoModel(t, f)
	if m.Busy() || cmd != nil {
		t.Fatal("the gate reclaimed a port answering as another service without asking")
	}
	if row, _ := m.Report().Find(KeyDaemon); row.Fix != FixStartDaemon {
		t.Errorf("the foreign record lost its `f` key: fix %v", row.Fix)
	}
}

func TestADeadDaemonRecordIsStartedAutomatically(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.StaleError{Kind: daemon.StalePIDDead, Path: "/tmp/i.json", Reason: "its pid 1 is not running"}
	if m, _ := newAutoModel(t, f); !m.Busy() {
		t.Error("a record left by a daemon that died at shutdown was not started over")
	}
}

func TestNothingStartsWhenLemonadeLivesOnAnotherMachine(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://10.20.30.40:13305")
	for name, f := range map[string]*fakeTransport{
		"daemon": func() *fakeTransport {
			f := newFake()
			f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
			return f
		}(),
		"agent": newFake().with("GET /daemon/v1/agents", 200, agentsStopped),
	} {
		t.Run(name, func(t *testing.T) {
			m, cmd := newAutoModel(t, f)
			if m.Busy() || cmd != nil {
				t.Fatal("the gate started something for a setup pointed at another machine")
			}
			blocker, _ := m.Report().Blocker()
			if blocker.Fix == FixNone {
				t.Error("the row lost its `f` key")
			}
		})
	}
}

func TestALoopbackLemonadeURLStillStarts(t *testing.T) {
	t.Setenv(lemonadeBaseURLEnv, "http://127.0.0.1:13305")
	f := newFake()
	f.attachErr = &daemon.NotRunningError{Path: "/tmp/instance.json"}
	if m, _ := newAutoModel(t, f); !m.Busy() {
		t.Error("a local LEMONADE_BASE_URL stopped the daemon from starting")
	}
}
