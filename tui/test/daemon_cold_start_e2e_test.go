package test

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
)

// The TUI used to hold the start lock while `gaia daemon start` ran, and that
// command waits on the same lock — so every cold start timed out. Only the real
// Python launcher takes the lock, so this runs it against this checkout's src/.
//
// Set GAIA_E2E_PYTHON to an interpreter with GAIA's dependencies; otherwise
// python3 on PATH is tried, and the test skips when it cannot import the daemon.
func TestColdStartThroughTheRealGaiaDaemonStart(t *testing.T) {
	if testing.Short() {
		t.Skip("spawns a real daemon")
	}
	python := os.Getenv("GAIA_E2E_PYTHON")
	if python == "" {
		p, err := exec.LookPath("python3")
		if err != nil {
			t.Skip("no python3 on PATH and GAIA_E2E_PYTHON is unset")
		}
		python = p
	}
	src, err := filepath.Abs(filepath.Join("..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	t.Setenv(daemon.EnvHome, home)
	env := append(os.Environ(), "PYTHONPATH="+src, daemon.EnvHome+"="+home)

	probe := exec.Command(python, "-c", "import gaia.cli, gaia.daemon.server")
	probe.Env = env
	if out, err := probe.CombinedOutput(); err != nil {
		t.Skipf("%s cannot import the GAIA daemon from %s: %v\n%s", python, src, err, out)
	}

	gaia := func(ctx context.Context, args ...string) *exec.Cmd {
		cmd := exec.CommandContext(ctx, python, append([]string{"-m", "gaia.cli", "daemon"}, args...)...)
		cmd.Env = env
		return cmd
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if out, err := gaia(ctx, "stop").CombinedOutput(); err != nil {
			t.Errorf("gaia daemon stop: %v\n%s", err, out)
		}
	})

	client := daemon.New(daemon.Options{
		StartCommand: func(ctx context.Context) (*exec.Cmd, error) { return gaia(ctx, "start"), nil },
		Logf:         t.Logf,
	})

	// Two callers at once must both succeed and land on the same daemon.
	const callers = 2
	var wg sync.WaitGroup
	insts := make([]*daemon.Instance, callers)
	errs := make([]error, callers)
	for i := range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			insts[i], errs[i] = client.StartOrAttach(context.Background())
		}()
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("caller %d could not cold-start the daemon: %v", i, err)
		}
	}
	if insts[0].PID != insts[1].PID {
		t.Errorf("concurrent callers started two daemons: pid %d and pid %d", insts[0].PID, insts[1].PID)
	}
}
