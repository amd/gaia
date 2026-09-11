// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package client

import (
	"errors"
	"fmt"
	"os/exec"
	"syscall"
)

// processGroup makes every descendant of the agent reachable by one kill.
//
// The shipped agent is a PyInstaller ONE-FILE binary: the process exec.Command
// starts is the bootloader, and the interpreter that runs the turn — and holds
// both ends of the pipe — is its child. Killing the bootloader alone leaves
// that child running the tool call the user just cancelled.
//
// On POSIX the child leads its own process group, so one signal to the negated
// pgid reaches the bootloader, the interpreter, and anything a tool spawned.
type processGroup struct {
	pgid int
}

func newProcessGroup() (*processGroup, error) { return &processGroup{}, nil }

// prepare must run BEFORE Start: the group is chosen at fork time.
func (g *processGroup) prepare(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.Setpgid = true
}

// attach records the group id, which Setpgid made equal to the child's pid.
func (g *processGroup) attach(cmd *exec.Cmd) error {
	if cmd.Process == nil {
		return fmt.Errorf("cannot group a process that was never started")
	}
	g.pgid = cmd.Process.Pid
	return nil
}

// terminate kills every process in the group. A group that has already gone
// (ESRCH) is the outcome this asked for, not a failure.
func (g *processGroup) terminate() error {
	if g.pgid == 0 {
		return fmt.Errorf("no agent process group was recorded, so nothing could be stopped")
	}
	if err := syscall.Kill(-g.pgid, syscall.SIGKILL); err != nil && !errors.Is(err, syscall.ESRCH) {
		return fmt.Errorf("could not stop the agent's process group %d: %w", g.pgid, err)
	}
	return nil
}

// close has nothing to release: the group is an attribute of the child, not a
// handle this process holds.
func (g *processGroup) close() {}
