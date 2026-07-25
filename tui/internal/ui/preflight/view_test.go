package preflight

import (
	"context"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
)

// renderAt drives the model to a settled report and returns the plain-text
// screen at the given size. Styling is stripped so the assertions are about
// what a monochrome terminal shows — colour must never be the only signal.
func renderAt(t *testing.T, f *fakeTransport, w, h int) (Model, []string) {
	t.Helper()
	m := New(f, EmailConfig(), Options{ManualProceed: true})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: w, Height: h})
	m = updated.(Model)
	updated, _ = m.Update(reportMsg{rep: Check(context.Background(), f, EmailConfig())})
	m = updated.(Model)
	return m, strings.Split(ansi.Strip(m.View()), "\n")
}

func assertFits(t *testing.T, lines []string, w, h int) {
	t.Helper()
	if len(lines) > h {
		t.Errorf("screen is %d rows, does not fit %d:\n%s", len(lines), h, strings.Join(lines, "\n"))
	}
	for i, l := range lines {
		if got := ansi.StringWidth(l); got > w {
			t.Errorf("line %d is %d columns, does not fit %d: %q", i, got, w, l)
		}
	}
}

func joined(lines []string) string { return strings.Join(lines, "\n") }

func splitLines(screen string) []string { return strings.Split(screen, "\n") }

// countMarkedRows counts checklist rows by their state marker — the text signal
// that has to survive every width.
func countMarkedRows(lines []string) int {
	n := 0
	for _, l := range lines {
		for _, marker := range []string{"[ok]", "[!]", "[?]", "[ ]", "[..]"} {
			if strings.Contains(l, marker) {
				n++
				break
			}
		}
	}
	return n
}

func TestRenderAllFailedAt80x24(t *testing.T) {
	f := newFake()
	f.attachErr = &daemon.NotRunningError{Path: "/Users/you/.gaia/host/instance.json"}

	m, lines := renderAt(t, f, 80, 24)
	screen := joined(lines)
	t.Logf("\n%s", screen)

	assertFits(t, lines, 80, 24)

	// Every row is present and legible without colour.
	for _, want := range []string{
		"Getting Email ready",
		"Background service",
		"Email agent",
		"Local AI",
		"AI model",
		"Mailbox",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("screen does not show %q:\n%s", want, screen)
		}
	}
	// The failure is marked in TEXT, carries a remedy, and offers its fix key.
	for _, want := range []string{"[!]", "gaia daemon start", "f start it for me", "esc back"} {
		if !strings.Contains(screen, want) {
			t.Errorf("screen does not show %q:\n%s", want, screen)
		}
	}
	// Rows that could not be checked say so — never a blank that reads as fine.
	if !strings.Contains(screen, "checked once") {
		t.Errorf("pending rows do not say what they are waiting on:\n%s", screen)
	}
	if m.Ready() {
		t.Error("model reports ready with a failed daemon")
	}
}

func TestRenderAllReadyAt80x24(t *testing.T) {
	m, lines := renderAt(t, newFake(), 80, 24)
	screen := joined(lines)
	t.Logf("\n%s", screen)

	assertFits(t, lines, 80, 24)

	if !m.Ready() {
		t.Fatalf("expected ready:\n%s", m.Report())
	}
	for _, want := range []string{
		"ready",
		"[ok]",
		"running (pid 41822)",
		"0.5.0",
		"Lemonade 8.1.10",
		"Gemma-4-E4B-it-GGUF · 16K context",
		"you@gmail.com (Gmail) · can send",
		"Starting Email…",
	} {
		if !strings.Contains(screen, want) {
			t.Errorf("screen does not show %q:\n%s", want, screen)
		}
	}
	if strings.Contains(screen, "[!]") {
		t.Errorf("an all-ready screen shows a failure marker:\n%s", screen)
	}
}

// The screen has to survive every failure mode, not just the one that fits.
func TestRenderFitsEveryScenarioAt80x24(t *testing.T) {
	scenarios := map[string]*fakeTransport{
		"lemonade unreachable": newFake().with("GET /v1/email/init", 503, initUnreachable),
		"lemonade too old":     newFake().with("GET /v1/email/init", 503, initTooOld),
		"version unknown":      newFake().with("GET /v1/email/init", 200, initUnknownVersion),
		"model missing":        newFake().with("GET /v1/email/init", 503, initModelMissing),
		"no mailbox":           newFake().with("GET /v1/email/connectors", 200, connectorsNone),
		"send not granted":     newFake().with("GET /v1/email/connectors", 200, connectorsNoSend),
		"sidecar stopped":      newFake().with("GET /daemon/v1/agents", 200, agentsStopped),
		"not installed":        newFake().with("GET /daemon/v1/agents", 200, agentsEmpty),
	}
	for name, f := range scenarios {
		t.Run(name, func(t *testing.T) {
			_, lines := renderAt(t, f, 80, 24)
			t.Logf("\n%s", joined(lines))
			assertFits(t, lines, 80, 24)
		})
	}
}

// A narrow or short terminal must still show the checklist and the key hints —
// the footer is the last thing to go, never the first.
func TestRenderSurvivesSmallTerminals(t *testing.T) {
	sizes := []struct{ w, h int }{{80, 24}, {60, 20}, {50, 14}, {40, 12}, {30, 10}}
	f := newFake().with("GET /v1/email/init", 503, initUnreachable)
	for _, s := range sizes {
		_, lines := renderAt(t, f, s.w, s.h)
		w, h := s.w, s.h
		if w < minW {
			w = minW
		}
		if h < minH {
			h = minH
		}
		assertFits(t, lines, w, h)
		screen := joined(lines)
		if !strings.Contains(screen, "esc") {
			t.Errorf("%dx%d lost the key hints:\n%s", s.w, s.h, screen)
		}
		// The promise: the remedy shrinks, the checklist never does. Labels
		// themselves may be truncated on a narrow terminal — a missing ROW is the
		// failure, a shortened label is not.
		if got := countMarkedRows(lines); got != 5 {
			t.Errorf("%dx%d shows %d checklist rows, want 5:\n%s", s.w, s.h, got, screen)
		}
	}
}

// `d` shows the raw probe answer — what a user pastes into a bug report.
func TestDetailsShowsTheRawAnswer(t *testing.T) {
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)
	m, _ := renderAt(t, f, 80, 24)

	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'d'}})
	m = updated.(Model)
	screen := ansi.Strip(m.View())

	if !strings.Contains(screen, "not downloaded") {
		t.Errorf("details does not show the raw hint:\n%s", screen)
	}
	assertFits(t, strings.Split(screen, "\n"), 80, 24)
}

func TestKeysDriveTheRightActions(t *testing.T) {
	t.Run("esc cancels", func(t *testing.T) {
		m, _ := renderAt(t, newFake(), 80, 24)
		_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
		if cmd == nil {
			t.Fatal("esc produced no command")
		}
		if _, ok := cmd().(CancelMsg); !ok {
			t.Fatalf("esc produced %T, want CancelMsg", cmd())
		}
	})

	t.Run("f on a mailbox row hands off to the connector flow", func(t *testing.T) {
		f := newFake().with("GET /v1/email/connectors", 200, connectorsNone)
		m, _ := renderAt(t, f, 80, 24)
		if m.FocusKey() != KeyMailbox {
			t.Fatalf("focus = %q, want the first row needing attention", m.FocusKey())
		}
		_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
		msg, ok := cmd().(ConnectMailboxMsg)
		if !ok {
			t.Fatalf("f produced %T, want ConnectMailboxMsg", cmd())
		}
		if msg.Provider != "google" || msg.AgentID != "email" {
			t.Errorf("connect message = %+v", msg)
		}
	})

	t.Run("f on a row with no safe fix says what to run instead", func(t *testing.T) {
		f := newFake().with("GET /v1/email/init", 503, initUnreachable)
		m, _ := renderAt(t, f, 80, 24)
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'f'}})
		m = updated.(Model)
		if !strings.Contains(ansi.Strip(m.View()), "lemonade-server serve") {
			t.Errorf("pressing f on an unfixable row said nothing useful:\n%s", m.View())
		}
	})

	t.Run("enter is refused while a check is blocked", func(t *testing.T) {
		f := newFake().with("GET /v1/email/connectors", 200, connectorsNone)
		m, _ := renderAt(t, f, 80, 24)
		_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
		if cmd != nil {
			t.Fatalf("enter proceeded past a blocked report: %T", cmd())
		}
	})

	t.Run("enter proceeds when only an indeterminate row remains", func(t *testing.T) {
		f := newFake().with("GET /v1/email/init", 200, initUnknownVersion)
		m, _ := renderAt(t, f, 80, 24)
		_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEnter})
		if cmd == nil {
			t.Fatal("enter did nothing on an unknown-but-not-blocked report")
		}
		if _, ok := cmd().(ProceedMsg); !ok {
			t.Fatalf("enter produced %T, want ProceedMsg", cmd())
		}
	})

	t.Run("up and down move the cursor", func(t *testing.T) {
		m, _ := renderAt(t, newFake(), 80, 24)
		updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyDown})
		m = updated.(Model)
		if m.FocusKey() != KeySidecar {
			t.Errorf("focus after down = %q, want %q", m.FocusKey(), KeySidecar)
		}
		updated, _ = m.Update(tea.KeyMsg{Type: tea.KeyUp})
		m = updated.(Model)
		if m.FocusKey() != KeyDaemon {
			t.Errorf("focus after up = %q, want %q", m.FocusKey(), KeyDaemon)
		}
	})
}

// An all-ready report auto-proceeds; nothing else does.
func TestAutoProceedOnlyWhenReady(t *testing.T) {
	f := newFake()
	m := New(f, EmailConfig(), Options{ReadyHold: 1})
	updated, cmd := m.Update(reportMsg{rep: Check(context.Background(), f, EmailConfig())})
	m = updated.(Model)
	if cmd == nil {
		t.Fatal("a ready report did not schedule the hand-off to chat")
	}
	if _, ok := cmd().(proceedTickMsg); !ok {
		t.Fatalf("ready report scheduled %T", cmd())
	}
	_, cmd = m.Update(proceedTickMsg{})
	if _, ok := cmd().(ProceedMsg); !ok {
		t.Fatalf("the hold produced %T, want ProceedMsg", cmd())
	}

	blocked := newFake().with("GET /v1/email/connectors", 200, connectorsNone)
	m2 := New(blocked, EmailConfig(), Options{ReadyHold: 1})
	_, cmd = m2.Update(reportMsg{rep: Check(context.Background(), blocked, EmailConfig())})
	if cmd != nil {
		t.Fatalf("a blocked report scheduled %T", cmd())
	}
}

func TestRenderWrapsLongRemediesInsteadOfOverflowing(t *testing.T) {
	// The mailbox remedy is the longest string the screen ever shows: a full
	// `gaia connectors connect` line with a scope URL.
	f := newFake().with("GET /v1/email/connectors", 200, connectorsNone)
	_, lines := renderAt(t, f, 80, 24)
	screen := joined(lines)
	assertFits(t, lines, 80, 24)

	// Wrapping must not lose the command: the whole thing has to be readable.
	flat := strings.Join(strings.Fields(screen), " ")
	if !strings.Contains(flat, "gaia connectors connect google --grant-agent installed:email") {
		t.Errorf("the connect command did not survive wrapping:\n%s", screen)
	}
	if !strings.Contains(flat, "https://www.googleapis.com/auth/gmail.send") {
		t.Errorf("the scope did not survive wrapping:\n%s", screen)
	}
}
