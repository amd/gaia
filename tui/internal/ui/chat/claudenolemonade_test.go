// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiainit"
)

// The regression these guard: `gaia run gaia --use-claude --claude-model
// claude-haiku-4-5` started LemonadeServer.exe anyway.
//
// The first-boot gate ran `gaia init` on every flagship launch, and `gaia
// init` auto-starts the local server unconditionally (_auto_start_server,
// src/gaia/installer/init_command.py). Claude mode only added
// --skip-chat-model, which skips the model DOWNLOAD and nothing else — so the
// one flag whose entire purpose is "do not use the local backend" was the
// flag that reliably brought it up, and blocked the first Claude answer
// behind a multi-minute install while it did.

// runInitCmds executes whatever Init() scheduled, so a gate that fires shows
// up as a real call rather than as unread intent.
func runInitCmds(t *testing.T, m ChatModel) {
	t.Helper()
	cmd := m.Init()
	if cmd == nil {
		return
	}
	drainCmd(t, cmd)
}

// drainCmd runs a Cmd and everything tea.Batch folded into it — Init returns
// a batch, and the setup probe is one member of it.
func drainCmd(t *testing.T, cmd tea.Cmd) {
	t.Helper()
	switch msg := cmd().(type) {
	case tea.BatchMsg:
		for _, c := range msg {
			if c != nil {
				drainCmd(t, c)
			}
		}
	case []tea.Cmd:
		for _, c := range msg {
			if c != nil {
				drainCmd(t, c)
			}
		}
	}
}

// A Claude session must not reach for the `gaia` CLI at all: every route to
// LemonadeServer.exe from this package runs through it.
func TestClaudeLaunchNeverInvokesTheGaiaCLI(t *testing.T) {
	called := false
	orig := gaiainit.Binary
	gaiainit.Binary = func() (string, error) {
		called = true
		t.Error("a --use-claude launch resolved the gaia CLI — that path starts " +
			"LemonadeServer.exe via `gaia init`")
		return "", nil
	}
	t.Cleanup(func() { gaiainit.Binary = orig })

	m := claudeLaunchModel(t, "claude-haiku-4-5")
	runInitCmds(t, m)

	if called {
		t.Fatal("the local backend must not be started for a Claude session")
	}
}

// The gate is skipped, not merely unarmed: setupChecking is what holds the
// composer and the initial --query, so leaving it set would hang a session
// whose probe now never runs.
func TestClaudeLaunchDoesNotArmTheSetupGate(t *testing.T) {
	m := claudeLaunchModel(t, "claude-haiku-4-5")

	if m.setupChecking {
		t.Error("a Claude session must not arm the first-boot gate — nothing " +
			"will ever answer it, so the composer would stay held")
	}
	if m.setupRunning {
		t.Error("a Claude session must not start a setup run")
	}
}

// Skipping is not the same as hiding. The session says on screen that the
// local server was not started and what that costs, because the embedder RAG
// and memory need has no Anthropic equivalent.
func TestClaudeLaunchSaysTheLocalBackendWasNotStarted(t *testing.T) {
	m := claudeLaunchModel(t, "claude-haiku-4-5")

	var transcript strings.Builder
	for _, msg := range m.messages {
		transcript.WriteString(msg.Content)
		transcript.WriteString("\n")
	}
	text := transcript.String()

	for _, must := range []string{
		"not started",      // what did not happen
		"Claude Haiku 4.5", // which model it runs on instead
		"gaia init",        // the command that sets the local half up
		"/setup",           // the in-TUI way to do the same
	} {
		if !strings.Contains(text, must) {
			t.Errorf("the launch notice must mention %q, got:\n%s", must, text)
		}
	}
}

// A LOCAL flagship launch is untouched — it still needs the gate, and this
// change must not have quietly disabled first-boot setup for everyone.
//
// It also proves the check above can actually fail: the same harness, on the
// same constructor, DOES reach the gaia CLI when Claude mode is off. Without
// this, "gaiainit.Binary was never called" would pass just as happily against a
// model that never calls anything.
func TestALocalFlagshipLaunchStillRunsTheSetupGate(t *testing.T) {
	called := false
	orig := gaiainit.Binary
	gaiainit.Binary = func() (string, error) { called = true; return orig2Path(), nil }
	t.Cleanup(func() { gaiainit.Binary = orig })

	m := gaiaTestModel(t)
	if !m.setupChecking {
		t.Fatal("a local flagship launch must still run first-boot setup")
	}
	runInitCmds(t, m)
	if !called {
		t.Error("the local path must still reach the gaia CLI — otherwise the " +
			"Claude-path assertion proves nothing")
	}
}

// orig2Path is a path that certainly does not exist, so the probe fails fast
// instead of running whatever `gaia` is installed on the test machine.
func orig2Path() string {
	return filepath.Join(os.TempDir(), "gaia-does-not-exist-for-tests")
}
