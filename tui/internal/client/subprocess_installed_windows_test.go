//go:build windows

package client

import (
	"errors"
	"os"
	"os/exec"
	"testing"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"

	"github.com/amd/gaia/tui/internal/daemon"
)

// These run against the RELEASED agent — the PyInstaller one-file build whose
// bootloader/child split is why kill() targets the whole tree. They are
// opt-in: point GAIA_AGENT_BINARY at an installed gaia-agent.exe.

func installedAgent(t *testing.T) string {
	t.Helper()
	bin := os.Getenv("GAIA_AGENT_BINARY")
	if bin == "" {
		t.Skip("set GAIA_AGENT_BINARY to an installed gaia-agent.exe to run this")
	}
	t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
	return bin
}

// childPIDs lists the live processes whose parent is pid.
func childPIDs(t *testing.T, pid uint32) []uint32 {
	t.Helper()
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		t.Fatalf("process snapshot: %v", err)
	}
	defer windows.CloseHandle(snap)
	var kids []uint32
	entry := windows.ProcessEntry32{Size: uint32(unsafe.Sizeof(windows.ProcessEntry32{}))}
	for err = windows.Process32First(snap, &entry); err == nil; err = windows.Process32Next(snap, &entry) {
		if entry.ParentProcessID == pid {
			kids = append(kids, entry.ProcessID)
		}
	}
	if !errors.Is(err, windows.ERROR_NO_MORE_FILES) {
		t.Fatalf("walking the process snapshot: %v", err)
	}
	return kids
}

func waitForChildren(t *testing.T, boot uint32) []uint32 {
	t.Helper()
	var kids []uint32
	waitFor(t, "the bootloader to start the real agent", 30*time.Second, func() bool {
		kids = childPIDs(t, boot)
		return len(kids) > 0
	})
	t.Logf("bootloader pid %d, agent child pids %v", boot, kids)
	return kids
}

// The before-state, kept as a test so the reason for the tree kill stays
// demonstrable. Launched the way the transport used to launch it — a plain
// exec, no job — a bare Process.Kill leaves the real agent running.
func TestInstalledAgentOutlivesABareKill(t *testing.T) {
	cmd := exec.Command(installedAgent(t))
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := cmd.StdoutPipe(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatalf("start the installed agent: %v", err)
	}
	boot := uint32(cmd.Process.Pid)
	kids := waitForChildren(t, boot)
	defer func() {
		for _, k := range kids {
			if p, err := os.FindProcess(int(k)); err == nil {
				_ = p.Kill()
			}
		}
		stdin.Close()
		_ = cmd.Wait()
	}()

	if err := cmd.Process.Kill(); err != nil {
		t.Fatalf("kill the bootloader: %v", err)
	}
	time.Sleep(time.Second)
	for _, k := range kids {
		if !daemon.PIDAlive(int(k)) {
			t.Fatalf("agent child %d died with its bootloader — the one-file split this guards against is gone", k)
		}
	}
	t.Logf("agent child pids %v still alive 1s after bootloader %d was killed", kids, boot)
}

func TestInstalledAgentTreeDiesOnKill(t *testing.T) {
	c := NewSubprocessClient(installedAgent(t), nil, false)
	c.mu.Lock()
	st, err := c.startLocked()
	c.mu.Unlock()
	if err != nil {
		t.Fatalf("start the installed agent: %v", err)
	}
	boot := st.proc.cmd.Process.Pid
	kids := waitForChildren(t, uint32(boot))

	if err := st.proc.kill(); err != nil {
		t.Fatalf("kill: %v", err)
	}
	for _, k := range kids {
		k := int(k)
		waitFor(t, "the agent child to die", 5*time.Second, func() bool { return !daemon.PIDAlive(k) })
	}
	waitFor(t, "the bootloader to die", 5*time.Second, func() bool { return !daemon.PIDAlive(boot) })
	t.Logf("tree kill: bootloader %d and agent children %v are gone", boot, kids)
	st.proc.reap()
	c.discard(st.proc, nil)
}
