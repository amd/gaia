// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package control

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// The control plane runs for the whole session, alongside a Bubble Tea program
// holding the alt screen. Anything it writes to stderr lands ON that frame:
// the terminal is not ours to write to between p.Run() and its return.
//
// That is not a cosmetic problem. --dev turns control debugging on (see
// run() in internal/ui/app.go), and the recorder logs once per control request,
// so driving a --dev session over the API repaints the screen with [control]
// lines faster than the TUI can redraw it — the frame a developer turned --dev
// on to READ is the frame the logging destroys.
//
// So diagnostics go to a file instead, the same split internal/ui/chat/devlog.go
// already draws for the agent's work log: one truncated pointer on screen, the
// full record in ~/.gaia/logs/. A failure to open that file drops the line
// rather than falling back to stderr — a lost diagnostic costs less than a
// corrupted session, and stderr is exactly what this exists to avoid.

// LogFileName is the session log the control plane appends its diagnostics to.
const LogFileName = "gaia-tui.log"

var (
	logMu   sync.Mutex
	logOnce sync.Once
	logFile *os.File
)

// LogDir returns the directory holding LogFileName.
//
// GAIA_TUI_HOME wins when set, so a test (or a second TUI started with a
// private home, the pattern that keeps two sessions from colliding) writes its
// diagnostics beside its own control.json rather than into the user's real
// ~/.gaia/logs.
func LogDir() (string, error) {
	if override := os.Getenv(EnvHome); override != "" {
		return override, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("cannot locate the home directory for %s: %w (set %s to choose one explicitly)",
			filepath.Join("~", ".gaia", "logs"), err, EnvHome)
	}
	return filepath.Join(home, ".gaia", "logs"), nil
}

// LogPath is the full path of the session log.
func LogPath() (string, error) {
	dir, err := LogDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, LogFileName), nil
}

// openLog resolves and opens the session log once per process. Every failure
// leaves logFile nil, which logf treats as "drop the line" — never as a reason
// to write to the terminal.
func openLog() {
	path, err := LogPath()
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return
	}
	// Appended, not truncated: a session log that erased the previous run would
	// destroy the record of the crash someone is reading it to explain.
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600) // #nosec G304 -- path derived from the home dir or GAIA_TUI_HOME
	if err != nil {
		return
	}
	logFile = f
}

// logf appends one timestamped diagnostic line to the session log.
//
// Safe from any goroutine: the HTTP handlers each run on their own, and an
// interleaved write would corrupt the log the same way stderr corrupts the
// screen.
func logf(format string, args ...any) {
	logMu.Lock()
	defer logMu.Unlock()
	logOnce.Do(openLog)
	if logFile == nil {
		return
	}
	fmt.Fprintf(logFile, "%s %s\n",
		time.Now().Format("2006-01-02 15:04:05.000"),
		fmt.Sprintf(format, args...))
}

// CloseLog releases the session log. Optional — the OS closes it at exit — but
// it lets a test reopen a fresh file under a new GAIA_TUI_HOME.
func CloseLog() {
	logMu.Lock()
	defer logMu.Unlock()
	if logFile != nil {
		_ = logFile.Close()
		logFile = nil
	}
	logOnce = sync.Once{}
}
