// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build darwin || linux

package attention

import (
	"os/exec"
	"strings"
	"testing"
	"time"
)

func TestHelperThatFailsAtOnceIsAnError(t *testing.T) {
	_, err := startHelper(exec.Command("sh", "-c", "echo no logind >&2; exit 1"), nil, func(error) {})
	if err == nil || !strings.Contains(err.Error(), "no logind") {
		t.Fatalf("got %v, want the helper's own words", err)
	}
}

func TestHelperThatDiesMidTurnIsReported(t *testing.T) {
	lost := make(chan error, 1)
	release, err := startHelper(exec.Command("sh", "-c", "sleep 0.6"), nil, func(e error) { lost <- e })
	if err != nil {
		t.Fatal(err)
	}
	select {
	case e := <-lost:
		if !strings.Contains(e.Error(), "middle of a turn") {
			t.Fatalf("got %v", e)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a helper that died was never reported")
	}
	if err := release(); err != nil {
		t.Fatalf("release after death: %v", err)
	}
}

func TestReleasedHelperIsNotReportedLost(t *testing.T) {
	lost := make(chan error, 1)
	cmd := exec.Command("cat")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	release, err := startHelper(cmd, stdin, func(e error) { lost <- e })
	if err != nil {
		t.Fatal(err)
	}
	if err := release(); err != nil {
		t.Fatal(err)
	}
	select {
	case e := <-lost:
		t.Fatalf("a deliberate release was reported as lost: %v", e)
	case <-time.After(300 * time.Millisecond):
	}
}
