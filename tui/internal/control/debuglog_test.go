// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// The bug this guards: Debugf used to write to stderr, and --dev turns control
// debugging on for the whole session. Every control request then repainted the
// live alt screen with a [control] line — the frame --dev exists to let a
// developer read is the frame the logging destroyed.
func TestDebugfWritesToTheLogNotStderr(t *testing.T) {
	home := t.TempDir()
	t.Setenv(EnvHome, home)
	CloseLog() // drop any handle a previous test opened under a different home
	t.Cleanup(CloseLog)

	Debugf(true)("hello %s", "world")
	CloseLog() // flush by closing before we read

	raw, err := os.ReadFile(filepath.Join(home, LogFileName))
	if err != nil {
		t.Fatalf("expected the diagnostic in %s: %v", LogFileName, err)
	}
	if !strings.Contains(string(raw), "[control] hello world") {
		t.Errorf("log missing the diagnostic, got: %q", raw)
	}
}

// Debug off must stay silent everywhere, including the file: an always-on log
// would grow without anyone asking for it.
func TestDebugfOffWritesNothing(t *testing.T) {
	home := t.TempDir()
	t.Setenv(EnvHome, home)
	CloseLog()
	t.Cleanup(CloseLog)

	Debugf(false)("hello %s", "world")
	CloseLog()

	if _, err := os.Stat(filepath.Join(home, LogFileName)); !os.IsNotExist(err) {
		t.Errorf("expected no log file with debug off, stat gave: %v", err)
	}
}

// An unopenable log drops the line. It must never fall back to stderr — that
// fallback is the corruption this file exists to prevent — and must never panic.
func TestLogfSurvivesAnUnwritableDir(t *testing.T) {
	// A path whose parent is a FILE cannot be created as a directory on any OS.
	blocker := filepath.Join(t.TempDir(), "not-a-dir")
	if err := os.WriteFile(blocker, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(EnvHome, filepath.Join(blocker, "nested"))
	CloseLog()
	t.Cleanup(CloseLog)

	Debugf(true)("this must not panic")
}

// The HTTP handlers each log from their own goroutine, so an unguarded writer
// would interleave and corrupt the log the same way stderr corrupts the screen.
func TestLogfIsConcurrencySafe(t *testing.T) {
	home := t.TempDir()
	t.Setenv(EnvHome, home)
	CloseLog()
	t.Cleanup(CloseLog)

	debug := Debugf(true)
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			debug("line from a handler goroutine")
		}()
	}
	wg.Wait()
	CloseLog()

	raw, err := os.ReadFile(filepath.Join(home, LogFileName))
	if err != nil {
		t.Fatal(err)
	}
	got := strings.Count(string(raw), "line from a handler goroutine")
	if got != 50 {
		t.Errorf("want 50 intact lines, got %d", got)
	}
}
