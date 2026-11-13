// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiaautomation

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
)

// stubGaia installs a fake `gaia` that prints stdout, exits with code, and
// records its argv next to itself.
func stubGaia(t *testing.T, stdout string, code int) (argsFile string) {
	t.Helper()
	dir := t.TempDir()
	out := filepath.Join(dir, "out.txt")
	argsFile = filepath.Join(dir, "args.txt")
	if err := os.WriteFile(out, []byte(stdout), 0o644); err != nil {
		t.Fatal(err)
	}
	var path, script string
	if runtime.GOOS == "windows" {
		path = filepath.Join(dir, "gaia.cmd")
		script = "@echo off\r\necho %*> \"" + argsFile + "\"\r\n" +
			"type \"" + out + "\"\r\nexit /b " + strconv.Itoa(code) + "\r\n"
	} else {
		path = filepath.Join(dir, "gaia")
		script = "#!/bin/sh\necho \"$@\" > '" + argsFile + "'\ncat '" + out +
			"'\nexit " + strconv.Itoa(code) + "\n"
	}
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	original := Binary
	Binary = func() (string, error) { return path, nil }
	t.Cleanup(func() { Binary = original })
	return argsFile
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimSpace(string(data))
}

const listJSON = `some log line
[
  {"id": "a1b2c3d4", "name": "Inbox digest", "kind": "summary", "status": "active",
   "next": "Fri 07 Nov 08:00", "every": "1 day", "prompt": "Summarize my inbox"},
  {"id": "e5f6a7b8", "name": "Heartbeat", "kind": "alert", "status": "paused",
   "next": "Fri 07 Nov 08:30", "every": "30 minutes", "failing_checks": 2},
  {"id": "watch:amd/gaia", "name": "GitHub activity on amd/gaia", "kind": "watch", "status": "active"},
  {"id": "waiting:9f8e7d6c", "name": "Inbox digest", "kind": "waiting",
   "status": "ran Fri 08:00; reaches your Discord DM when the Discord bridge runs"}
]
`

// A waiting message shares its automation's name; listed, it would take that
// name's place, so "/automations pause Inbox digest" would reach the message.
func TestListReadsAutomationsAndLeavesOutWatchesAndWaitingMessages(t *testing.T) {
	args := stubGaia(t, listJSON, 0)
	items, err := List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got := read(t, args); got != "automation list" {
		t.Errorf("argv = %q, want automation list", got)
	}
	if len(items) != 2 {
		t.Fatalf("got %d items, want 2 (the watch and the waiting message left out): %+v", len(items), items)
	}
	if items[0].Name != "Inbox digest" || items[0].Every != "1 day" || items[0].Paused() {
		t.Errorf("first item = %+v", items[0])
	}
	if !items[1].Paused() || !items[1].Heartbeat() || items[1].FailingChecks != 2 {
		t.Errorf("second item = %+v", items[1])
	}
}

func TestAnEmptyListIsNoItemsNotAnError(t *testing.T) {
	stubGaia(t, "[]\n", 0)
	items, err := List(context.Background())
	if err != nil || len(items) != 0 {
		t.Fatalf("items=%v err=%v", items, err)
	}
}

func TestAFailedListIsUnansweredNotEmpty(t *testing.T) {
	stubGaia(t, "❌ automations.json is not usable\n", 1)
	_, err := List(context.Background())
	if !errors.Is(err, ErrUnanswered) {
		t.Fatalf("err = %v, want ErrUnanswered", err)
	}
	if !strings.Contains(err.Error(), "automations.json is not usable") {
		t.Errorf("err does not carry GAIA's reason: %v", err)
	}
}

func TestSetPassesTheActionAndRef(t *testing.T) {
	args := stubGaia(t, "Paused: 'Inbox digest'.\n", 0)
	said, err := Set(context.Background(), ActionPause, "a1b2c3d4")
	if err != nil {
		t.Fatal(err)
	}
	if got := read(t, args); got != "automation pause -- a1b2c3d4" {
		t.Errorf("argv = %q", got)
	}
	if said != "Paused: 'Inbox digest'." {
		t.Errorf("said = %q", said)
	}
}

func TestSetReturnsTheCLIsReason(t *testing.T) {
	stubGaia(t, "❌ No automation is called or numbered 'x'.\n", 2)
	_, err := Set(context.Background(), ActionRemove, "x")
	if err == nil || err.Error() != "No automation is called or numbered 'x'." {
		t.Fatalf("err = %v", err)
	}
}

func TestSetRefusesAnUnknownAction(t *testing.T) {
	stubGaia(t, "", 0)
	if _, err := Set(context.Background(), "run", "x"); err == nil {
		t.Fatal("run is not one of these commands' actions")
	}
}

func TestListSkipsAStrayBracketBeforeTheJSON(t *testing.T) {
	stubGaia(t, "[warn] slow start\n[\n  {\"id\": \"a1\", \"name\": \"X\", \"kind\": \"summary\", \"status\": \"active\"}\n]\n", 0)
	items, err := List(context.Background())
	if err != nil || len(items) != 1 || items[0].ID != "a1" {
		t.Fatalf("items=%+v err=%v", items, err)
	}
}
