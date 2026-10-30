package client

import (
	"bufio"
	"fmt"
	"os"
	"slices"
	"testing"
	"time"
)

// slowExitDelay outlasts closeGrace, so a Close that killed after closeGrace
// would cut the child off mid-save.
const slowExitDelay = closeGrace + time.Second

func TestSubprocessCloseHelper(t *testing.T) {
	// Scanned, not indexed from the end: spawnArgs appends
	// BypassPermissionsFlag in bypass mode, which shifts both sentinels and
	// would silently turn this helper into a no-op the parent only sees as a
	// turn timeout.
	flag := slices.Index(os.Args, "--close-helper")
	if flag < 0 || flag+1 >= len(os.Args) {
		return
	}
	mode := os.Args[flag+1]
	scanner := bufio.NewScanner(os.Stdin)
	if !scanner.Scan() {
		os.Exit(1)
	}
	fmt.Println(`{"type":"answer","content":"ok"}`)
	for scanner.Scan() {
	}
	switch mode {
	case "ignore-eof":
		time.Sleep(time.Hour)
	case "slow-exit":
		// The agent saving the last turn's memories before it exits.
		time.Sleep(slowExitDelay)
	}
	os.Exit(0)
}

func startCloseHelper(t *testing.T, mode string, exitGrace time.Duration) (*SubprocessClient, *procHandle) {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1/api/v1")
	c := NewSubprocessClient(exe, []string{"-test.run=^TestSubprocessCloseHelper$", "--", "--close-helper", mode}, false)
	c.exitGrace = exitGrace
	runTurn(t, c, "hello")
	proc := c.proc
	t.Cleanup(func() { _ = proc.kill(); proc.reap() })
	return c, proc
}

func closeWithin(t *testing.T, c *SubprocessClient, bound time.Duration) time.Duration {
	t.Helper()
	start := time.Now()
	closed := make(chan error, 1)
	go func() { closed <- c.Close() }()
	select {
	case err := <-closed:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(bound):
		t.Fatalf("Close did not return within %s", bound)
	}
	return time.Since(start)
}

func TestSubprocessClient_CloseAfterCompletedTurn(t *testing.T) {
	for _, mode := range []string{"exit-on-eof", "ignore-eof"} {
		t.Run(mode, func(t *testing.T) {
			c, proc := startCloseHelper(t, mode, closeGrace)
			closeWithin(t, c, 3*closeGrace)
			if proc.cmd.ProcessState == nil {
				t.Fatal("Close returned without waiting for the child to exit")
			}
			if mode == "exit-on-eof" && !proc.cmd.ProcessState.Success() {
				t.Fatal("cooperative child was killed instead of exiting on stdin EOF")
			}
			if err := c.Close(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

// A child still saving memories when stdin closes must be let finish: a kill
// after closeGrace drops the last turn's facts on every quick quit.
func TestSubprocessClient_CloseWaitsForAGracefulExitUpToTheBudget(t *testing.T) {
	budget := slowExitDelay + 3*time.Second
	c, proc := startCloseHelper(t, "slow-exit", budget)
	took := closeWithin(t, c, budget+2*closeGrace)
	if !proc.cmd.ProcessState.Success() {
		t.Fatalf("the child was killed mid-save after %s instead of exiting on its own", took)
	}
	if took < slowExitDelay {
		t.Fatalf("Close returned after %s, before the child finished saving", took)
	}
}

// The wait is bounded: a child that never exits is killed once the budget runs
// out, so quitting cannot hang.
func TestSubprocessClient_CloseKillsAChildThatOutlastsTheBudget(t *testing.T) {
	const budget = 3 * time.Second
	c, proc := startCloseHelper(t, "ignore-eof", budget)
	took := closeWithin(t, c, budget+3*closeGrace)
	if took < budget {
		t.Fatalf("Close killed the child after %s, before the %s budget", took, budget)
	}
	if proc.cmd.ProcessState == nil || proc.cmd.ProcessState.Success() {
		t.Fatal("a child that ignored EOF past the budget was not killed")
	}
}

func TestNewSubprocessClientCoversTheAgentsMemoryDrain(t *testing.T) {
	// The Python agent drains memory for up to EXTRACTION_EXIT_WAIT_S (15 s).
	if got := NewSubprocessClient("agent", nil, false).exitGrace; got <= 15*time.Second {
		t.Fatalf("exit grace %s does not cover the agent's 15 s memory drain", got)
	}
}
