package preflight

import (
	"context"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/daemon"
)

// TestLiveReadiness runs the real gate against whatever this machine actually
// has, through the real daemon client. It SKIPS when no daemon is attachable,
// so it is a no-op in CI and a real end-to-end check on a developer box.
//
// It asserts contract shape, never readiness: a machine with no mailbox
// connected is a perfectly valid state for this test to observe. What it proves
// is that the paths, headers, and JSON shapes this package sends are the ones
// the daemon and the sidecar actually accept — which no fake can prove.
func TestLiveReadiness(t *testing.T) {
	tr := NewDaemonTransport(daemon.New(daemon.Options{
		Logf: func(format string, args ...any) { t.Logf(format, args...) },
	}))

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if _, err := tr.Attach(ctx); err != nil {
		t.Skipf("no attachable GAIA daemon on this machine: %v", err)
	}

	rep := Check(ctx, tr, EmailConfig())
	t.Logf("\n%s", rep)

	if len(rep.Rows) != 5 {
		t.Fatalf("expected 5 rows, got %d", len(rep.Rows))
	}
	for _, row := range rep.Rows {
		if row.State == StateFailed {
			assertRealCommand(t, row)
		}
		if row.Line == "" {
			t.Errorf("row %q rendered no status line", row.Key)
		}
	}

	m := New(tr, EmailConfig(), Options{ManualProceed: true})
	updated, _ := m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})
	updated, _ = updated.(Model).Update(reportMsg{rep: rep})
	screen := ansi.Strip(updated.(Model).View())
	t.Logf("\n%s", screen)
	assertFits(t, splitLines(screen), 80, 24)
}

// TestColdMachineThroughTheRealClient is the first-run state: no daemon has ever
// registered, so ~/.gaia/host/instance.json does not exist. It runs the REAL
// daemon client (no fake) and needs no daemon, so it is deterministic in CI —
// and it is the one path a developer box, which always has a warm instance.json,
// can never reproduce on its own.
func TestColdMachineThroughTheRealClient(t *testing.T) {
	t.Setenv(daemon.EnvHome, t.TempDir())

	// Nothing is launched here: Check only ever attaches, so the client's start
	// path is never reached.
	tr := NewDaemonTransport(daemon.New(daemon.Options{
		Logf: func(format string, args ...any) { t.Logf(format, args...) },
	}))
	rep := Check(context.Background(), tr, EmailConfig())
	t.Logf("\n%s", rep)

	if len(rep.Rows) != 5 {
		t.Fatalf("cold machine produced %d rows, want 5", len(rep.Rows))
	}
	row, _ := rep.Find(KeyDaemon)
	if row.State != StateFailed {
		t.Fatalf("daemon row on a cold machine = %s, want failed", row.State.Word())
	}
	if row.Fix != FixStartDaemon {
		t.Errorf("a never-started daemon does not offer to start one")
	}
	assertRealCommand(t, row)
	for _, other := range rep.Rows[1:] {
		if other.State != StatePending {
			t.Errorf("row %q is %s on a machine with no daemon; nothing else is knowable",
				other.Key, other.State.Word())
		}
	}
}
