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

	"github.com/amd/gaia/tui/internal/gaiaautomation"
)

func sampleAutomations() []gaiaautomation.Item {
	return []gaiaautomation.Item{
		{ID: "a1b2c3d4", Name: "Inbox digest", Kind: "summary", Status: "active",
			Next: "Fri 07 Nov 08:00", Every: "1 day", Prompt: "Summarize my inbox", QuietHours: "22:00-07:00"},
		{ID: "e5f6a7b8", Name: "Heartbeat", Kind: "alert", Status: "paused", Next: "Fri 07 Nov 08:30", Every: "30 minutes"},
		{ID: "c1c1c1c1", Name: "Call Sam", Kind: "reminder", Status: "active", Next: "17:00", Says: "Call Sam"},
		{ID: "d2d2d2d2", Name: "Main CI", Kind: "trigger", Status: "active", Next: "08:15", Every: "15 minutes",
			Check: "gaia automation ci-status --repo amd/gaia --branch main", FailingChecks: 3},
	}
}

func TestAutomationsListsEachOneNumbered(t *testing.T) {
	m := slackModel(t)
	updated, cmd := m.handleAutomationsCommand("/automations")
	if cmd == nil {
		t.Fatal("/automations must read the list")
	}
	next, _ := updated.(ChatModel).handleAutomationsList(automationsListMsg{items: sampleAutomations()})
	after := next.(ChatModel)
	note := lastStatus(t, after)
	for _, want := range []string{
		"Automations (4):",
		"1. Inbox digest — summary, every 1 day, next Fri 07 Nov 08:00",
		"2. Heartbeat — heartbeat, every 30 minutes, paused",
		"3. Call Sam — reminder, once, 17:00",
		"checks: gaia automation ci-status",
		"its check has failed 3 time(s) in a row",
		"quiet 22:00-07:00",
		"/automations pause 1",
	} {
		if !strings.Contains(note, want) {
			t.Errorf("list is missing %q:\n%s", want, note)
		}
	}
	if fmt.Sprint(after.automationRefs) != "[a1b2c3d4 e5f6a7b8 c1c1c1c1 d2d2d2d2]" {
		t.Errorf("refs = %v", after.automationRefs)
	}
}

func TestAnEmptyListSaysHowToAddOne(t *testing.T) {
	next, _ := slackModel(t).handleAutomationsList(automationsListMsg{})
	if note := lastStatus(t, next.(ChatModel)); !strings.Contains(note, "No automations yet") {
		t.Errorf("note = %q", note)
	}
}

// stubAutomationCLI swaps in a fake `gaia` that records its argv and prints out.
func stubAutomationCLI(t *testing.T, out string) (argsFile string) {
	t.Helper()
	dir := t.TempDir()
	outFile := filepath.Join(dir, "out.txt")
	argsFile = filepath.Join(dir, "args.txt")
	if err := os.WriteFile(outFile, []byte(out), 0o644); err != nil {
		t.Fatal(err)
	}
	var path, script string
	if runtime.GOOS == "windows" {
		path = filepath.Join(dir, "gaia.cmd")
		script = "@echo off\r\necho %*>> \"" + argsFile + "\"\r\ntype \"" + outFile + "\"\r\n"
	} else {
		path = filepath.Join(dir, "gaia")
		script = "#!/bin/sh\necho \"$@\" >> '" + argsFile + "'\ncat '" + outFile + "'\n"
	}
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	original := gaiaautomation.Binary
	gaiaautomation.Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { gaiaautomation.Binary = original })
	return argsFile
}

func TestANumberChangesTheOneThatWasListed(t *testing.T) {
	args := stubAutomationCLI(t, "[]\n")
	m := slackModel(t)
	m.automationRefs = []string{"a1b2c3d4", "e5f6a7b8"}
	_, cmd := m.handleAutomationsCommand("/automations pause 2")
	if cmd == nil {
		t.Fatal("pause must run")
	}
	msg := cmd().(automationsListMsg)
	if msg.err != nil {
		t.Fatal(msg.err)
	}
	data, err := os.ReadFile(args)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Fields(strings.ReplaceAll(string(data), "\r", ""))
	if strings.Join(lines, " ") != "automation pause -- e5f6a7b8 automation list" {
		t.Errorf("argv = %q, want the pause of the 2nd listed id, then a fresh list", lines)
	}
}

func TestANameIsPassedThroughAsIs(t *testing.T) {
	args := stubAutomationCLI(t, "[]\n")
	_, cmd := slackModel(t).handleAutomationsCommand("/automations remove Inbox digest")
	cmd()
	data, _ := os.ReadFile(args)
	if !strings.HasPrefix(strings.ReplaceAll(string(data), `"`, ""), "automation remove -- Inbox digest") {
		t.Errorf("argv = %q", data)
	}
}

func TestANumberOutsideTheListIsRefusedWithoutRunning(t *testing.T) {
	m := slackModel(t)
	m.automationRefs = []string{"a1b2c3d4"}
	updated, cmd := m.handleAutomationsCommand("/automations remove 5")
	if cmd != nil {
		t.Error("an unknown number must not run anything")
	}
	if note := lastStatus(t, updated.(ChatModel)); !strings.Contains(note, "no automation 5") {
		t.Errorf("note = %q", note)
	}
}

func TestAMissingRefAsksWhichOne(t *testing.T) {
	updated, cmd := slackModel(t).handleAutomationsCommand("/automations resume")
	if cmd != nil {
		t.Error("nothing to change without a ref")
	}
	if note := lastStatus(t, updated.(ChatModel)); !strings.Contains(note, "Say which one") {
		t.Errorf("note = %q", note)
	}
}

func TestAnUnknownSubcommandShowsHelp(t *testing.T) {
	updated, cmd := slackModel(t).handleAutomationsCommand("/automations run 1")
	if cmd != nil {
		t.Error("help runs nothing")
	}
	note := lastStatus(t, updated.(ChatModel))
	for _, want := range []string{"/automations pause 2", "/automations resume 2", "/automations remove 2"} {
		if !strings.Contains(note, want) {
			t.Errorf("help is missing %q", want)
		}
	}
}

func TestAChangeShowsItsConfirmationAboveTheList(t *testing.T) {
	next, _ := slackModel(t).handleAutomationsList(automationsListMsg{
		said: "Paused: 'Inbox digest'.", items: sampleAutomations()[:1],
	})
	note := lastStatus(t, next.(ChatModel))
	if !strings.HasPrefix(note, "Paused: 'Inbox digest'.\n\nAutomations (1):") {
		t.Errorf("note = %q", note)
	}
}

func TestAnUnreadableListIsNotShownAsEmpty(t *testing.T) {
	err := fmt.Errorf("%w: boom", gaiaautomation.ErrUnanswered)
	next, _ := slackModel(t).handleAutomationsList(automationsListMsg{err: err})
	note := lastStatus(t, next.(ChatModel))
	if strings.Contains(note, "No automations") || !strings.Contains(note, "Could not read your automations") {
		t.Errorf("note = %q", note)
	}
}

func TestSubmitRoutesAutomationsInsteadOfAskingTheAgent(t *testing.T) {
	for _, q := range []string{"/automations", "/automations pause 1"} {
		if !isAutomationsCommand(q) {
			t.Errorf("%q is not routed", q)
		}
	}
	if isAutomationsCommand("/automationsx") {
		t.Error("/automationsx must not be routed")
	}
}

func TestAListedNameWinsOverItsNumber(t *testing.T) {
	args := stubAutomationCLI(t, "[]\n")
	m := slackModel(t)
	next, _ := m.handleAutomationsList(automationsListMsg{items: []gaiaautomation.Item{
		{ID: "aaaa1111", Name: "Inbox digest", Kind: "summary", Status: "active"},
		{ID: "bbbb2222", Name: "Heartbeat", Kind: "alert", Status: "active"},
		{ID: "cccc3333", Name: "2", Kind: "reminder", Status: "active"},
	}})
	_, cmd := next.(ChatModel).handleAutomationsCommand("/automations pause 2")
	cmd()
	data, _ := os.ReadFile(args)
	if !strings.HasPrefix(string(data), "automation pause -- cccc3333") {
		t.Errorf("argv = %q, want the one named 2, not the second listed", data)
	}
}

func TestATypedFlagIsANameNotRemoveAll(t *testing.T) {
	args := stubAutomationCLI(t, "[]\n")
	_, cmd := slackModel(t).handleAutomationsCommand("/automations remove --all")
	cmd()
	data, _ := os.ReadFile(args)
	if !strings.HasPrefix(string(data), "automation remove -- --all") {
		t.Errorf("argv = %q, want --all passed after --, as a name", data)
	}
}
