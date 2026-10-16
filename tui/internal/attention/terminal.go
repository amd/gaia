// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"os"
	"sync"
)

// Terminal is the program's output with one lock around every write. Bubble
// Tea renders from its own goroutine, so a notification written straight to
// stdout could land inside a frame's escape sequence and corrupt the screen;
// through Terminal it can only land between two whole writes.
//
// It satisfies term.File, so Bubble Tea still sees a terminal it can size and
// put in raw mode.
type Terminal struct {
	mu sync.Mutex
	f  *os.File
}

// NewTerminal wraps f, normally os.Stdout.
func NewTerminal(f *os.File) *Terminal { return &Terminal{f: f} }

func (t *Terminal) Write(p []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.f.Write(p)
}

func (t *Terminal) Read(p []byte) (int, error) { return t.f.Read(p) }

func (t *Terminal) Fd() uintptr { return t.f.Fd() }

// Close does not close the file: stdout belongs to the process, not to the
// program that borrowed it.
func (t *Terminal) Close() error { return nil }
