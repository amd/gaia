package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/status"
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
	updated, cmd = m.Update(m.quickFixCmd(KeyDaemon, FixStartDaemon, startTimeout)())
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

// A tick scheduled by a ready report must not launch an agent the user backed
// out of in the 800 ms before it fired.
func TestProceedTickAfterCancelDoesNotLaunch(t *testing.T) {
	f := newFake()
	m := New(f, EmailConfig(), Options{ReadyHold: time.Second})
	updated, cmd := m.Update(reportMsg{rep: Check(t.Context(), f, EmailConfig())})
	m = updated.(Model)
	if cmd == nil {
		t.Fatal("a ready report did not schedule the hand-off")
	}

	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(Model)

	if _, tick := m.Update(proceedTickMsg{}); tick != nil {
		t.Fatalf("a stale tick launched the agent after esc: %T", tick())
	}
}

// esc must leave the screen usable: a phase left at "provisioning" makes Busy()
// true forever and a re-shown gate rejects every key.
func TestCancelLeavesTheScreenUsable(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)
	m := newModel(t, f)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
	m = updated.(Model)
	if !m.Busy() {
		t.Fatal("the download did not mark the screen busy")
	}

	updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(Model)
	if m.Busy() {
		t.Error("after esc the screen is still busy, so r/f/enter would be refused")
	}

	// And the keys really do work again.
	if _, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'r'}}); cmd == nil {
		t.Error("r was refused on a screen the user came back to")
	}
}

// A pull that ends without a terminal event still owes the user a cause and a
// remedy — "Download failed." with nothing after it is the failure mode the
// fail-loudly rule exists to prevent.
func TestAClosedProvisionChannelStillExplainsItself(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)
	m := newModel(t, f)
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
	m = updated.(Model)

	ch := m.provisionCh
	if ch == nil {
		t.Fatal("no provisioning channel")
	}
	// Simulate the producer closing without delivering a result.
	closed := make(chan provisionEvent)
	close(closed)
	msg := waitProvision(closed, m.cfg)
	ev, ok := msg().(provisionMsg)
	if !ok {
		t.Fatalf("waitProvision produced %T", msg())
	}
	if !ev.event.done {
		t.Fatal("a closed channel did not produce a terminal event")
	}
	if ev.event.result.Diagnosis.Cause == "" || ev.event.result.Diagnosis.Command == "" {
		t.Fatalf("a closed channel produced an empty diagnosis: %+v", ev.event.result.Diagnosis)
	}

	updated, _ = m.Update(provisionMsg{ch: m.provisionCh, event: ev.event})
	m = updated.(Model)
	note := strings.Join(strings.Fields(ansi.Strip(m.View())), " ")
	if !strings.Contains(note, "gaia init") {
		t.Errorf("the failed download does not tell the user what to run:\n%s", note)
	}
}

// The terminal result must survive a context that expired at the same moment —
// a uniform select would drop it half the time.
func TestSendPrefersDeliveryOverACancelledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	for i := 0; i < 200; i++ {
		ch := make(chan provisionEvent, 1)
		send(ctx, ch, provisionEvent{done: true, result: ProvisionResult{OK: true}})
		select {
		case ev := <-ch:
			if !ev.done {
				t.Fatal("delivered the wrong event")
			}
		default:
			t.Fatalf("iteration %d: the result was dropped despite a free buffer", i)
		}
	}
}

// Pressing enter on a blocked report must say why, not silently do nothing.
//
// A stopped sidecar, not a bad mailbox: a mailbox the agent can repair in the
// conversation is offerable, so it would no longer refuse here.
func TestEnterOnABlockedReportSaysWhy(t *testing.T) {
	f := newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
	m := newModel(t, f)

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	if cmd != nil {
		t.Fatalf("enter proceeded past a blocked report: %T", cmd())
	}
	screen := strings.Join(strings.Fields(ansi.Strip(m.View())), " ")
	if !strings.Contains(screen, "cannot start yet") || !strings.Contains(screen, "agent") {
		t.Errorf("enter was a silent no-op:\n%s", screen)
	}
}

// Nothing failed but something could not be verified: the launch proceeds (the
// sidecar does not treat it as fatal) while naming what went unproven.
//
// This exercises the DispositionNotify path specifically — the unadvertised
// Lemonade version is one of the two BLOCKER-1 guard rows. It is pinned with
// an explicit Disposition assertion so a future change that accidentally
// flips this row's Disposition to Halt fails HERE, with a message that says
// why, rather than only surfacing as a silent behaviour change a user
// reports weeks later.
func TestAnIndeterminateReportProceedsButSaysWhatItCouldNotVerify(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 200, initUnknownVersion)
	rep := Check(t.Context(), f, EmailConfig())
	if row, ok := rep.Find(KeyLemonade); !ok || row.Disposition != status.DispositionNotify {
		t.Fatalf("test setup: want the lemonade row DispositionNotify, got %+v", row)
	}

	m := New(f, EmailConfig(), Options{ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	updated, cmd := updated.(Model).Update(reportMsg{rep: rep})
	m = updated.(Model)

	if cmd == nil {
		t.Fatal("an unblocked report did not schedule the hand-off")
	}
	if m.Ready() {
		t.Fatal("an indeterminate row was counted as ready")
	}
	screen := strings.Join(strings.Fields(ansi.Strip(m.View())), " ")
	if !strings.Contains(screen, "Starting anyway") || !strings.Contains(screen, "Local AI") {
		t.Errorf("the hand-off does not name what could not be verified:\n%s", screen)
	}
	if _, ok := cmd().(proceedTickMsg); !ok {
		t.Fatalf("scheduled %T", cmd())
	}
}

// The reported bug: a DispositionHalt row (the ctx shortfall) must not
// auto-proceed. tea.Cmd is func() tea.Msg and Go can only compare it to nil,
// so this asserts the absence of the automatic path behaviourally, in three
// parts, matching the acceptance criterion exactly.
func TestADispositionHaltRowNoLongerAutoProceeds(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 200, initCtxShortfall)
	rep := Check(t.Context(), f, EmailConfig())
	if rep.Blocked() || rep.Ready() {
		t.Fatalf("test setup: want Blocked()==false && Ready()==false, got Blocked=%v Ready=%v",
			rep.Blocked(), rep.Ready())
	}
	if !rep.HasHalt() {
		t.Fatal("test setup: want a Halt row present")
	}

	m := New(f, EmailConfig(), Options{ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	updated, cmd := updated.(Model).Update(reportMsg{rep: rep})
	m = updated.(Model)

	// 1. Whatever cmd is, it must not be a tick toward ProceedMsg. Run it
	// under a deadline (the idiom TestCancelStopsAnInFlightFix already uses
	// for in-flight work) so a regression to the 2500ms tick trips the
	// deadline instead of stalling the suite.
	if cmd != nil {
		done := make(chan tea.Msg, 1)
		go func() { done <- cmd() }()
		select {
		case msg := <-done:
			if _, ok := msg.(proceedTickMsg); ok {
				t.Fatal("reportMsg with a Halt row scheduled the auto-proceed tick")
			}
		case <-time.After(200 * time.Millisecond):
			t.Fatal("cmd from a Halt report did not resolve within 200ms — a regression to the 2500ms tick")
		}
	}

	// 2. No live path to ProceedMsg survives a stale tick landing anyway.
	if _, tick := m.Update(proceedTickMsg{}); tick != nil {
		t.Fatalf("a stray proceedTickMsg still launched the agent after a Halt report: %T", tick())
	}

	// 3. The deliberate path — pressing enter — still works: only the
	// automatic path was removed.
	updated, cmd = m.Update(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(Model)
	if cmd == nil {
		t.Fatal("enter on a Halt report produced no command")
	}
	if _, ok := cmd().(ProceedMsg); !ok {
		t.Fatalf("enter on a Halt report produced %T, want ProceedMsg", cmd())
	}
}

// Blocked() is untouched by the Halt/Notify split: a real failure never gets
// a tick, before or after this issue.
func TestBlockedReportSchedulesNoTick(t *testing.T) {
	f := newFake().with("GET /daemon/v1/agents", 200, agentsStopped)
	rep := Check(t.Context(), f, EmailConfig())
	if !rep.Blocked() {
		t.Fatal("test setup: want a blocked report")
	}

	m := New(f, EmailConfig(), Options{ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	_, cmd := updated.(Model).Update(reportMsg{rep: rep})

	if cmd != nil {
		t.Fatalf("a blocked report scheduled %T — Blocked() must never auto-proceed", cmd())
	}
}

// BLOCKER-1's other guard, at the model layer: 2+ linked mailboxes is a
// StateUnknown, DispositionNotify row (report.go/check.go), so it must still
// schedule the hold and proceed — never halt. A blanket rule here would fire
// on every launch with 2+ mailboxes linked, forever.
func TestMultiMailboxReportSchedulesTheHoldNotAHalt(t *testing.T) {
	f := newFake().with("GET /v1/email/connectors", 200, connectorsBoth)
	rep := Check(t.Context(), f, EmailConfig())
	if rep.Blocked() || rep.Ready() {
		t.Fatalf("test setup: want Blocked()==false && Ready()==false, got Blocked=%v Ready=%v",
			rep.Blocked(), rep.Ready())
	}
	if rep.HasHalt() {
		t.Fatal("test setup: guards BLOCKER-1 — multi-mailbox must never be a Halt row")
	}

	m := New(f, EmailConfig(), Options{ReadyHold: time.Millisecond})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	_, cmd := updated.(Model).Update(reportMsg{rep: rep})

	if cmd == nil {
		t.Fatal("a multi-mailbox report did not schedule the hand-off — BLOCKER-1 would fire on every launch with 2+ mailboxes linked")
	}
	if _, ok := cmd().(proceedTickMsg); !ok {
		t.Fatalf("multi-mailbox report scheduled %T, want proceedTickMsg", cmd())
	}
}

// Leaving the screen must cancel work that is ALREADY RUNNING, not just detach
// from it. An abandoned ensure keeps a sidecar-spawning request alive for its
// full 15-minute budget — the user sees a process start minutes after they
// walked away.
func TestCancelStopsAnInFlightFix(t *testing.T) {
	f := newFake().with("GET /daemon/v1/agents", 200, agentsStopped)

	started := make(chan struct{})
	finished := make(chan error, 1)
	f.ensureFn = func(ctx context.Context) error {
		close(started)
		<-ctx.Done() // a real ensure blocks here for as long as the daemon takes
		finished <- ctx.Err()
		return ctx.Err()
	}

	m := newModel(t, f)
	if m.FocusKey() != KeySidecar {
		t.Fatalf("focus = %q, want the sidecar row", m.FocusKey())
	}

	updated, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
	m = updated.(Model)
	if cmd == nil {
		t.Fatal("pressing f produced no command")
	}
	// The runtime executes a batch's children; do the same so the ensure is
	// genuinely in flight when esc arrives.
	if batch, ok := cmd().(tea.BatchMsg); ok {
		for _, c := range batch {
			go c() //nolint:errcheck // the message is irrelevant; the call is the point
		}
	} else {
		t.Fatalf("f produced %T, want a batch", cmd())
	}

	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("the ensure never started")
	}

	m.Update(tea.KeyMsg{Type: tea.KeyEsc})

	select {
	case err := <-finished:
		if !errors.Is(err, context.Canceled) {
			t.Errorf("the in-flight fix ended with %v, want a cancellation", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("esc did not cancel the in-flight fix — it is still running")
	}
}
