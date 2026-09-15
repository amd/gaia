package test

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/control"
	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/ui/root"
)

// readPidFile waits for path to name a pid other than `not`.
func readPidFile(t *testing.T, path string, not int) int {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for {
		if b, err := os.ReadFile(path); err == nil {
			if n, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil && n > 0 && n != not {
				return n
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("no pid other than %d appeared in %s within 15s", not, path)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// waitGone fails unless pid leaves the OS process table within 5s.
func waitGone(t *testing.T, what string, pid int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for daemon.PIDAlive(pid) {
		if time.Now().After(deadline) {
			t.Fatalf("%s (pid %d) is still in the process table 5s after the stop", what, pid)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// TestEscAgainstTheReleasedProcessShape drives the real TUI over the control
// API against the mock agent in the released binary's shape — a bootloader
// whose child runs the agent and holds the pipes — and reads the OS process
// table, not the screen, for what actually stopped.
func TestEscAgainstTheReleasedProcessShape(t *testing.T) {
	dir := t.TempDir()
	pidfile := filepath.Join(dir, "agent.pid")
	sideEffect := filepath.Join(dir, "tool-ran")
	t.Setenv("MOCKAGENT_MULTIPROCESS", "1")
	t.Setenv("MOCKAGENT_PIDFILE", pidfile)
	t.Setenv("MOCKAGENT_SIDE_EFFECT", sideEffect)
	t.Setenv("MOCKAGENT_TOOL_MS", "4000")

	tui := startLiveTUIWith(t, func(m root.FlagshipModel) root.FlagshipModel {
		return m.WithBypassPermissions(true)
	})
	tui.call(http.MethodPost, "/resize", map[string]any{"cols": 120, "rows": 40})
	tui.waitFor(map[string]any{"state": map[string]any{"view": control.ViewChat}, "timeout_ms": 20000})
	tui.waitFor(map[string]any{"contains": "BYPASS PERMISSIONS —"})

	ask := func(text string) {
		t.Helper()
		if status, body := tui.call(http.MethodPost, "/text", map[string]any{"text": text}); status != http.StatusOK {
			t.Fatalf("POST /text %q: status %d (%v)", text, status, body)
		}
		tui.keys("enter")
	}
	settled := func() {
		t.Helper()
		tui.waitFor(map[string]any{"state": map[string]any{"streaming": false}, "timeout_ms": 20000})
	}

	// /bypass off before the agent has even started must still reach it.
	ask("/bypass off")
	tui.waitFor(map[string]any{"absent": "BYPASS PERMISSIONS —"})
	ask("report bypass")
	tui.waitFor(map[string]any{"contains": "bypass=false pid=", "timeout_ms": 20000})
	settled()
	agent := readPidFile(t, pidfile, 0)
	boot := readPidFile(t, pidfile+".boot", 0)
	t.Logf("bootloader pid %d, agent pid %d", boot, agent)

	// 1. One Esc: the turn stops, and the agent — with the session it holds — stays.
	ask("run the slow tool")
	tui.waitFor(map[string]any{"contains": "rm -rf ./build", "timeout_ms": 20000})
	tui.keys("esc")
	tui.waitFor(map[string]any{"contains": "cancelled", "timeout_ms": 10000})
	settled()
	if !daemon.PIDAlive(agent) {
		t.Fatalf("one Esc killed the agent (pid %d)", agent)
	}
	ask("report bypass")
	tui.waitFor(map[string]any{"contains": fmt.Sprintf("bypass=false pid=%d turn=3", agent), "timeout_ms": 20000})
	settled()

	// 2. A call the agent cannot interrupt: the first Esc asks, the second
	// stops the whole tree before the call can finish.
	ask("run the stubborn slow tool")
	tui.waitFor(map[string]any{"contains": "rm -rf ./dist", "timeout_ms": 20000})
	toolStarted := time.Now()
	tui.keys("esc")
	tui.waitFor(map[string]any{"contains": "cancelling"})
	tui.keys("esc")
	tui.waitFor(map[string]any{"contains": "stopped the agent process"})
	waitGone(t, "the agent", agent)
	waitGone(t, "the bootloader", boot)
	t.Logf("after Esc Esc: agent %d and bootloader %d are out of the process table", agent, boot)

	time.Sleep(time.Until(toolStarted.Add(5 * time.Second)))
	if _, err := os.Stat(sideEffect); err == nil {
		t.Fatal("the stopped tool call ran to completion")
	}

	// 3. The restart comes up with /bypass still off, and says what it lost.
	ask("report bypass")
	tui.waitFor(map[string]any{"contains": "The agent was restarted", "timeout_ms": 20000})
	respawned := readPidFile(t, pidfile, agent)
	tui.waitFor(map[string]any{"contains": fmt.Sprintf("bypass=false pid=%d turn=1", respawned), "timeout_ms": 20000})
	settled()
	if screen := tui.screen(); strings.Contains(screen, "BYPASS PERMISSIONS —") {
		t.Errorf("the bypass banner came back after the restart:\n%s", screen)
	}
	t.Logf("restarted agent pid %d came up with bypass off", respawned)
}
