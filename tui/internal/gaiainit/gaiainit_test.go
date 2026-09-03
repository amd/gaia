package gaiainit

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestHelperProcess(t *testing.T) {
	if os.Getenv("GAIA_TUI_INIT_TEST_HELPER") != "1" {
		return
	}
	if pidPath := os.Getenv("GAIA_TUI_INIT_TEST_PID_FILE"); pidPath != "" {
		if err := os.WriteFile(pidPath, []byte(strconv.Itoa(os.Getpid())), 0o600); err != nil {
			fmt.Fprintf(os.Stderr, "could not write helper pid: %v\n", err)
			os.Exit(2)
		}
	}
	// The parent test deliberately reads only the first line. Keep writing so
	// the parent's stdout reader eventually blocks on its full event channel,
	// then stay alive until context cancellation kills this child.
	for i := 0; i < 100000; i++ {
		fmt.Fprintf(os.Stdout, "stdout line %d\n", i)
	}
	select {}
}

func TestStartCancellationStopsBlockedReadersAndReapsChild(t *testing.T) {
	oldBinary := Binary
	Binary = func() (string, error) { return os.Args[0], nil }
	t.Cleanup(func() { Binary = oldBinary })
	t.Setenv("GAIA_TUI_INIT_TEST_HELPER", "1")
	pidPath := filepath.Join(t.TempDir(), "helper.pid")
	t.Setenv("GAIA_TUI_INIT_TEST_PID_FILE", pidPath)
	baselineGoroutines := runtime.NumGoroutine()

	ch, cancel, err := Start(false)
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(cancel)

	select {
	case evt, ok := <-ch:
		if !ok || evt.Done {
			t.Fatal("init helper did not produce a live output event")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("init helper did not produce its first output event")
	}

	pidBytes, err := os.ReadFile(pidPath)
	if err != nil {
		t.Fatalf("read helper pid: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(pidBytes)))
	if err != nil {
		t.Fatalf("parse helper pid %q: %v", pidBytes, err)
	}
	cancel()

	if !waitForProcessExit(pid) {
		t.Fatalf("cancelled init helper process %d is still alive", pid)
	}
	if !waitForGoroutinesAtMost(baselineGoroutines + 1) {
		t.Fatalf(
			"cancelled init left reader goroutines behind: baseline=%d current=%d",
			baselineGoroutines,
			runtime.NumGoroutine(),
		)
	}
}

func waitForProcessExit(pid int) bool {
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for {
		if !processAlive(pid) {
			return true
		}
		select {
		case <-deadline.C:
			return false
		case <-ticker.C:
		}
	}
}

func waitForGoroutinesAtMost(limit int) bool {
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for {
		if runtime.NumGoroutine() <= limit {
			return true
		}
		select {
		case <-deadline.C:
			return false
		case <-ticker.C:
		}
	}
}

func processAlive(pid int) bool {
	if runtime.GOOS == "windows" {
		out, err := exec.Command(
			"tasklist",
			"/FI", fmt.Sprintf("PID eq %d", pid),
			"/FO", "CSV",
			"/NH",
		).Output()
		return err == nil && strings.Contains(string(out), fmt.Sprintf(",%q,", strconv.Itoa(pid)))
	}
	return exec.Command("kill", "-0", strconv.Itoa(pid)).Run() == nil
}