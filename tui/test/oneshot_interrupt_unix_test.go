// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package test

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// lockedBuffer is read by the test while the child process writes it.
type lockedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *lockedBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *lockedBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}

type liveOneShot struct {
	cmd     *exec.Cmd
	stderr  *lockedBuffer
	exited  chan error
	agent   int
	boot    int
	effect  string
	started time.Time
}

// startOneShot runs `gaia-tui run gaia --query <query>` against the mock agent
// in the released binary's shape, a bootloader whose child runs the turn, and
// returns once that child is mid-tool-call.
func startOneShot(t *testing.T, query string) *liveOneShot {
	t.Helper()
	gaiaBin, binDir := buildBinaries(t)
	dir := t.TempDir()
	pidfile := filepath.Join(dir, "agent.pid")
	effect := filepath.Join(dir, "tool-ran")

	cmd := exec.Command(gaiaBin, "run", "gaia", "--mock", filepath.Join(binDir, mockName()), "--query", query)
	cmd.Env = append(oneShotEnv(t, binDir),
		"MOCKAGENT_MULTIPROCESS=1",
		"MOCKAGENT_PIDFILE="+pidfile,
		"MOCKAGENT_SIDE_EFFECT="+effect,
		"MOCKAGENT_TOOL_MS=60000",
	)
	stderr := &lockedBuffer{}
	cmd.Stdout = &lockedBuffer{}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("start the one-shot: %v", err)
	}
	r := &liveOneShot{cmd: cmd, stderr: stderr, exited: make(chan error, 1), effect: effect}
	go func() { r.exited <- cmd.Wait() }()
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		for _, pid := range []int{r.agent, r.boot} {
			if pid > 0 {
				_ = syscall.Kill(pid, syscall.SIGKILL)
			}
		}
	})

	r.agent = readPidFile(t, pidfile, 0)
	r.boot = readPidFile(t, pidfile+".boot", 0)
	r.waitStderr(t, "bash_execute", 20*time.Second)
	return r
}

func (r *liveOneShot) waitStderr(t *testing.T, want string, within time.Duration) {
	t.Helper()
	deadline := time.Now().Add(within)
	for !strings.Contains(r.stderr.String(), want) {
		if time.Now().After(deadline) {
			t.Fatalf("the one-shot never printed %q within %s:\n%s", want, within, r.stderr.String())
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func (r *liveOneShot) interrupt(t *testing.T) {
	t.Helper()
	r.started = time.Now()
	if err := r.cmd.Process.Signal(os.Interrupt); err != nil {
		t.Fatalf("send SIGINT to the TUI: %v", err)
	}
}

// exitCode waits for the TUI to exit and returns its code.
func (r *liveOneShot) exitCode(t *testing.T, within time.Duration) int {
	t.Helper()
	select {
	case err := <-r.exited:
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return exitErr.ExitCode()
		}
		if err != nil {
			t.Fatalf("wait for the one-shot: %v", err)
		}
		return 0
	case <-time.After(within):
		t.Fatalf("the one-shot was still running %s after SIGINT:\n%s", within, r.stderr.String())
		return -1
	}
}

// assertAgentGone is the bug itself: the agent leads its own process group, so
// the terminal's SIGINT never reaches it, and it used to run the tool on.
func (r *liveOneShot) assertAgentGone(t *testing.T) {
	t.Helper()
	waitGone(t, "the agent bootloader", r.boot)
	waitGone(t, "the agent running the turn", r.agent)
	if _, err := os.Stat(r.effect); err == nil {
		t.Error("the tool ran to completion after Ctrl+C")
	}
}

// An agent that honours the stop ends the turn at once and exits 130.
func TestCtrlCStopsAOneShotTurnThroughTheAgent(t *testing.T) {
	r := startOneShot(t, "slow tool")
	r.interrupt(t)

	if code := r.exitCode(t, 8*time.Second); code != 130 {
		t.Fatalf("exit code = %d, want 130:\n%s", code, r.stderr.String())
	}
	if !strings.Contains(r.stderr.String(), "The agent ended the turn") {
		t.Errorf("stderr does not confirm the agent stopped:\n%s", r.stderr.String())
	}
	r.assertAgentGone(t)
}

// An agent that ignores the stop is killed, process group and all, once the
// grace runs out — in bounded time.
func TestCtrlCKillsAOneShotAgentThatIgnoresTheStop(t *testing.T) {
	r := startOneShot(t, "stubborn slow tool")
	r.interrupt(t)

	if code := r.exitCode(t, 25*time.Second); code != 130 {
		t.Fatalf("exit code = %d, want 130:\n%s", code, r.stderr.String())
	}
	if !strings.Contains(r.stderr.String(), "Stopping the agent process") {
		t.Errorf("stderr does not say the agent process was stopped:\n%s", r.stderr.String())
	}
	r.assertAgentGone(t)
}

// A second Ctrl+C quits without waiting out the grace, and still takes the
// agent down with it.
func TestASecondCtrlCQuitsAOneShotAtOnce(t *testing.T) {
	r := startOneShot(t, "stubborn slow tool")
	r.interrupt(t)
	r.waitStderr(t, "press Ctrl+C again", 5*time.Second)
	if err := r.cmd.Process.Signal(os.Interrupt); err != nil {
		t.Fatalf("send the second SIGINT: %v", err)
	}

	if code := r.exitCode(t, 8*time.Second); code != 130 {
		t.Fatalf("exit code = %d, want 130:\n%s", code, r.stderr.String())
	}
	if took := time.Since(r.started); took > 8*time.Second {
		t.Errorf("the second Ctrl+C took %s to quit", took)
	}
	r.assertAgentGone(t)
}

// SIGHUP (the terminal closing) and SIGTERM stop the turn the same way.
func TestHangupAndTermStopAOneShot(t *testing.T) {
	for _, sig := range []syscall.Signal{syscall.SIGHUP, syscall.SIGTERM} {
		t.Run(sig.String(), func(t *testing.T) {
			r := startOneShot(t, "slow tool")
			if err := r.cmd.Process.Signal(sig); err != nil {
				t.Fatalf("send %v: %v", sig, err)
			}
			if code := r.exitCode(t, 8*time.Second); code != 130 {
				t.Fatalf("exit code = %d, want 130:\n%s", code, r.stderr.String())
			}
			r.assertAgentGone(t)
		})
	}
}
