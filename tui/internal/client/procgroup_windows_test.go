// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build windows

package client

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"golang.org/x/sys/windows"

	"github.com/amd/gaia/tui/internal/daemon"
)

const (
	jobHelperEnv  = "GAIA_TEST_JOB_HELPER"
	jobSleeperEnv = "GAIA_TEST_JOB_SLEEPER"
	jobPidfileEnv = "GAIA_TEST_JOB_PIDFILE"
)

// TestJobHelperSpawner is not a test: run inside the agent's job, it starts a
// plain child (a tool) and one that asks to leave the job (GAIA's daemon).
func TestJobHelperSpawner(t *testing.T) {
	if os.Getenv(jobHelperEnv) != "1" {
		t.Skip("helper process for TestAgentJobKeepsOnlyABreakawayChild")
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	sleeper := func(flags uint32) int {
		cmd := exec.Command(exe, "-test.run=^TestJobHelperSleeper$")
		cmd.Env = append(os.Environ(), jobHelperEnv+"=", jobSleeperEnv+"=1")
		cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: flags}
		if err := cmd.Start(); err != nil {
			msg := "ERR starting a child with flags " + strconv.FormatUint(uint64(flags), 16) + ": " + err.Error()
			_ = os.WriteFile(os.Getenv(jobPidfileEnv), []byte(msg), 0o600)
			t.Fatal(msg)
		}
		return cmd.Process.Pid
	}
	plain := sleeper(0)
	away := sleeper(windows.CREATE_BREAKAWAY_FROM_JOB)
	pids := strconv.Itoa(plain) + " " + strconv.Itoa(away)
	if err := os.WriteFile(os.Getenv(jobPidfileEnv), []byte(pids), 0o600); err != nil {
		t.Fatal(err)
	}
	time.Sleep(60 * time.Second)
}

// TestJobHelperSleeper is not a test: it is the long-running child.
func TestJobHelperSleeper(t *testing.T) {
	if os.Getenv(jobSleeperEnv) != "1" {
		t.Skip("helper process for TestAgentJobKeepsOnlyABreakawayChild")
	}
	time.Sleep(60 * time.Second)
}

// The agent's job must reap what a cancelled turn started, but not GAIA's
// daemon: that daemon and its model server are shared by every session, and
// the session whose agent happened to start them used to take them down for
// all the others when it closed.
func TestAgentJobKeepsOnlyABreakawayChild(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	pidfile := filepath.Join(t.TempDir(), "pids")
	group, err := newProcessGroup()
	if err != nil {
		t.Fatal(err)
	}
	defer group.close()

	cmd := exec.Command(exe, "-test.run=^TestJobHelperSpawner$")
	cmd.Env = append(os.Environ(), jobHelperEnv+"=1", jobPidfileEnv+"="+pidfile)
	group.prepare(cmd)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if err := group.attach(cmd); err != nil {
		t.Fatal(err)
	}

	var plain, away int
	waitFor(t, "the helper to start both children", 30*time.Second, func() bool {
		b, err := os.ReadFile(pidfile)
		if err != nil {
			return false
		}
		if strings.HasPrefix(string(b), "ERR") {
			t.Fatalf("the agent's job refused a child that asked to leave it (%s): it "+
				"lacks JOB_OBJECT_LIMIT_BREAKAWAY_OK", b)
		}
		f := strings.Fields(string(b))
		if len(f) != 2 {
			return false
		}
		plain, _ = strconv.Atoi(f[0])
		away, _ = strconv.Atoi(f[1])
		return plain > 0 && away > 0
	})
	defer func() {
		if p, err := os.FindProcess(away); err == nil {
			_ = p.Kill()
		}
	}()

	if err := group.terminate(); err != nil {
		t.Fatal(err)
	}
	_ = cmd.Wait()
	waitFor(t, "the plain child to die with the job", 10*time.Second, func() bool {
		return !daemon.PIDAlive(plain)
	})
	time.Sleep(500 * time.Millisecond)
	if !daemon.PIDAlive(away) {
		t.Fatal("a child that asked to leave the agent's job died with it: the job " +
			"lacks JOB_OBJECT_LIMIT_BREAKAWAY_OK, so closing one session kills the " +
			"daemon and Lemonade Server every other session is using")
	}
}
