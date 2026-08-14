// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// gaiaTestModel is a flagship-agent model (agentID == setupAgentID), the one
// this gate applies to. Constructed through NewChatModelForCatalogAgent so
// applyFirstBootGate runs exactly like the real launch path.
func gaiaTestModel(t *testing.T) ChatModel {
	t.Helper()
	c := &nullClient{}
	m := NewChatModelForCatalogAgent(c, setupAgentID, "GAIA", false)
	m.width, m.height = 100, 30
	return m
}

// stubGaiaBinary points gaiaBinary at a fixed path for the duration of the
// test, so a test can drive startSetup/checkSetupCmd without depending on
// (or invoking) whatever `gaia` happens to be on the machine running the
// test suite.
func stubGaiaBinary(t *testing.T, path string) {
	t.Helper()
	orig := gaiaBinary
	gaiaBinary = func() (string, error) { return path, nil }
	t.Cleanup(func() { gaiaBinary = orig })
}

// --- dispatch: /setup is a command, never a question -----------------------

func TestSetupCommandIsNeverSentAsAQuery(t *testing.T) {
	if !isSetupCommand("/setup") {
		t.Error(`"/setup" must be recognised as a local command`)
	}
	if isSetupCommand("what does /setup do") {
		t.Error("a question about setup is still a question")
	}
}

func TestSetupIsListedInHelp(t *testing.T) {
	view := components.RenderHelpOverlay(components.HelpContextChat, "", 100, 40, 0)
	if !strings.Contains(view, "/setup") {
		t.Errorf("the chat help overlay does not list /setup:\n%s", view)
	}
}

// A flagship launch starts with the first-boot check armed; every other
// agent has no local setup step and applyFirstBootGate leaves it alone.
func TestOnlyTheFlagshipAgentArmsTheGate(t *testing.T) {
	if m := gaiaTestModel(t); !m.setupChecking {
		t.Error("the flagship agent must start with setupChecking true")
	}
	if m, _ := newTestModel(t); m.setupChecking {
		t.Error("a non-flagship agent must not arm the setup gate")
	}
}

// /setup on an agent with no local setup step says so and does not touch
// gaiaBinary at all -- there is nothing to run.
func TestSetupOnANonFlagshipAgentDeclines(t *testing.T) {
	m, _ := newTestModel(t)
	updated, cmd := m.submit("/setup")
	m = updated.(ChatModel)
	if cmd != nil {
		t.Fatal("declining must not start anything")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "does not have a local setup step") {
		t.Errorf("expected a decline note, got: %+v", last)
	}
}

// /setup while a run is already in flight must not start a second one.
func TestSetupAlreadyRunningDoesNotStartASecondOne(t *testing.T) {
	m := gaiaTestModel(t)
	m.setupRunning = true

	updated, cmd := m.submit("/setup")
	m = updated.(ChatModel)
	if cmd != nil {
		t.Fatal("must not launch a second `gaia init`")
	}
	last := m.messages[len(m.messages)-1]
	if !strings.Contains(last.Content, "already running") {
		t.Errorf("expected an 'already running' note, got: %+v", last)
	}
}

// A `gaia` binary that cannot even be started (missing, or the resolved path
// is bad) is surfaced as an error naming what to do next -- never silently
// dropped, and never mistaken for a running setup.
func TestSetupStartFailureIsSurfacedLoudly(t *testing.T) {
	stubGaiaBinary(t, filepath.Join(t.TempDir(), "does-not-exist"))

	m := gaiaTestModel(t)
	updated, _ := m.submit("/setup")
	m = updated.(ChatModel)

	if m.setupRunning {
		t.Error("a failed start must not claim to be running")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "Could not start setup") {
		t.Errorf("expected a surfaced start failure, got: %+v", last)
	}
	if !strings.Contains(last.Content, "gaia init") {
		t.Errorf("the failure must name the fallback command, got: %q", last.Content)
	}
}

// --- gaia CLI resolution -----------------------------------------------

func TestGaiaBinaryNotOnPathIsActionable(t *testing.T) {
	restore := gaiaBinary
	gaiaBinary = func() (string, error) {
		return "", errNotOnPath
	}
	t.Cleanup(func() { gaiaBinary = restore })

	_, _, err := startSetup(false)
	if err == nil || !strings.Contains(err.Error(), "not on PATH") {
		t.Errorf("expected an actionable PATH error, got: %v", err)
	}
}

var errNotOnPath = errTest("the `gaia` CLI is not on PATH")

// --- the composer holds Enter while the gate is up --------------------

func TestEnterQueuesWhileSetupIsChecking(t *testing.T) {
	m := gaiaTestModel(t)
	m.input.SetValue("hello")

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)

	if !queuedIs(m.queued, "hello") {
		t.Errorf("Enter must queue while the first-boot check is pending, got queued=%q", m.queued)
	}
}

func TestEnterQueuesWhileSetupIsRunning(t *testing.T) {
	m := gaiaTestModel(t)
	m.setupChecking = false
	m.setupRunning = true
	m.input.SetValue("hello")

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEnter})
	m = updated.(ChatModel)

	if !queuedIs(m.queued, "hello") {
		t.Errorf("Enter must queue while a setup run is in flight, got queued=%q", m.queued)
	}
}

// --- cancellation -------------------------------------------------------

// Esc during a run announces cancellation and asks the child to stop; the
// confirmed "cancelled" line is left to the run's own terminal event
// (handleSetupEvent), same two-step shape as the turn cancel flow.
func TestEscDuringSetupCancelsAndSaysSo(t *testing.T) {
	m := gaiaTestModel(t)
	cancelled := false
	m.setupRunning = true
	m.setupCancel = func() { cancelled = true }

	updated, _ := m.handleKey(tea.KeyMsg{Type: tea.KeyEsc})
	m = updated.(ChatModel)

	if !cancelled {
		t.Fatal("Esc must ask the setup subprocess to stop")
	}
	if !m.setupCancelRequested {
		t.Error("the cancel request must be recorded so the terminal event reports it correctly")
	}
	last := m.messages[len(m.messages)-1]
	if !strings.Contains(last.Content, "cancelling") {
		t.Errorf("Esc must say something happened immediately, got: %+v", last)
	}
}

// The run's own terminal event, once it lands, must report "cancelled" --
// not misread the killed child as an ordinary failure.
func TestSetupTerminalEventAfterCancelSaysCancelled(t *testing.T) {
	m := gaiaTestModel(t)
	m.setupRunning = true
	m.setupCancelRequested = true

	updated, _ := m.handleSetupEvent(setupEvent{done: true, err: errTest("signal: killed")})
	m = updated.(ChatModel)

	if m.setupRunning {
		t.Error("a terminal event must clear setupRunning")
	}
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "cancelled") {
		t.Errorf("expected a 'cancelled' status line, got: %+v", last)
	}
}

// A genuine failure (not requested) is reported as an error with a way to
// retry, never silently swallowed.
func TestSetupTerminalEventOnFailureIsAnError(t *testing.T) {
	m := gaiaTestModel(t)
	m.setupRunning = true

	updated, _ := m.handleSetupEvent(setupEvent{done: true, err: errTest("exit status 1")})
	m = updated.(ChatModel)

	last := m.messages[len(m.messages)-1]
	if last.Role != RoleError || !strings.Contains(last.Content, "Setup failed") {
		t.Errorf("expected a surfaced failure, got: %+v", last)
	}
	if !strings.Contains(last.Content, "/setup") {
		t.Errorf("the failure must say how to retry, got: %q", last.Content)
	}
}

// --- Claude mode threads --skip-chat-model through ----------------------

func TestClaudeModeSkipsTheChatModelInSetupArgs(t *testing.T) {
	args := setupRunArgs(true)
	found := false
	for _, a := range args {
		if a == "--skip-chat-model" {
			found = true
		}
	}
	if !found {
		t.Errorf("claude mode must pass --skip-chat-model, got: %v", args)
	}

	args = setupRunArgs(false)
	for _, a := range args {
		if a == "--skip-chat-model" {
			t.Errorf("a non-claude session must not pass --skip-chat-model, got: %v", args)
		}
	}
}

func TestClaudeModeSkipsTheChatModelInCheckArgs(t *testing.T) {
	args := setupCheckArgs(true)
	found := false
	for _, a := range args {
		if a == "--skip-chat-model" {
			found = true
		}
	}
	if !found {
		t.Errorf("claude mode's check must pass --skip-chat-model, got: %v", args)
	}
}

// An installed `gaia` older than `gaia init --check` rejects the flag and exits
// 2 with "unrecognized arguments". That was read as "not set up yet", so the
// TUI ran a full multi-minute `gaia init` — including a vite production build
// of the web UI — on EVERY launch of an already-working machine.
//
// Only exit 1 means "not ready". Anything else means the question was never
// answered, and must say so rather than silently reinstalling.
func TestAnUnknownCheckFlagIsAnErrorNotACleanMachine(t *testing.T) {
	script := writeExitScript(t, "old-gaia", 2,
		"usage: gaia [-h] ...\ngaia: error: unrecognized arguments: --check")
	stubGaiaBinary(t, script)

	msg := checkSetupCmd(false)()
	res, ok := msg.(setupCheckResultMsg)
	if !ok {
		t.Fatalf("unexpected message %T", msg)
	}
	if res.ready {
		t.Fatal("an unusable check reported the machine as READY")
	}
	if res.err == nil {
		t.Fatal("exit 2 was read as 'not set up yet' — this reinstalls on every launch")
	}
	if !strings.Contains(res.err.Error(), "unrecognized arguments") {
		t.Errorf("the error does not quote what gaia actually said: %v", res.err)
	}
}

// Exit 1 is the documented negative and must still mean "run setup".
func TestExitOneStillMeansNotSetUp(t *testing.T) {
	stubGaiaBinary(t, writeExitScript(t, "not-ready", 1, "NOT READY"))

	res := checkSetupCmd(false)().(setupCheckResultMsg)
	if res.err != nil {
		t.Fatalf("the documented not-ready exit was treated as a failure: %v", res.err)
	}
	if res.ready {
		t.Error("exit 1 reported as ready")
	}
}

func TestExitZeroMeansReady(t *testing.T) {
	stubGaiaBinary(t, writeExitScript(t, "ready", 0, "READY: profile 'chat' is already set up"))

	res := checkSetupCmd(false)().(setupCheckResultMsg)
	if res.err != nil || !res.ready {
		t.Errorf("a ready machine was not reported ready: ready=%v err=%v", res.ready, res.err)
	}
}

// writeExitScript builds a tiny executable that prints output and exits with a
// chosen code, so the check can be driven without the real CLI.
func writeExitScript(t *testing.T, name string, code int, output string) string {
	t.Helper()
	dir := t.TempDir()
	if runtime.GOOS == "windows" {
		path := filepath.Join(dir, name+".bat")
		body := "@echo off\r\n"
		for _, line := range strings.Split(output, "\n") {
			body += "echo " + line + "\r\n"
		}
		body += fmt.Sprintf("exit /b %d\r\n", code)
		if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
			t.Fatalf("write stub: %v", err)
		}
		return path
	}
	path := filepath.Join(dir, name+".sh")
	body := fmt.Sprintf("#!/bin/sh\ncat <<'EOS'\n%s\nEOS\nexit %d\n", output, code)
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatalf("write stub: %v", err)
	}
	return path
}
