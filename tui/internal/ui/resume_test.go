// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/sessionstore"
)

func flagship() catalog.Agent {
	return catalog.Agent{ID: catalog.FlagshipID, Transport: catalog.TransportSubprocess}
}

func seedIndex(t *testing.T, id string, pid int) {
	t.Helper()
	dir, err := sessionstore.Dir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{
		"version": 1, "id": id, "title": "Earlier", "kind": "chat", "status": "done",
		"created_at": 1.0, "updated_at": 2.0, "pid": pid, "turns": 1,
	})
	if err := os.WriteFile(filepath.Join(dir, id+".json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	line := `{"user":"hi","answer":"hello"}` + "\n"
	if err := os.WriteFile(filepath.Join(dir, id+".jsonl"), []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestResumeTargetReadsTheNamedSession(t *testing.T) {
	seedIndex(t, "feed0001", 0)
	got, err := resumeTarget(flagship(), Resume{ID: "feed0001"})
	if err != nil {
		t.Fatal(err)
	}
	if got.session.ID != "feed0001" || len(got.turns) != 1 || got.turns[0].Answer != "hello" {
		t.Fatalf("got %+v", got)
	}
}

func TestResumeTargetRefusesWhatCannotBeReopened(t *testing.T) {
	seedIndex(t, "feed0002", 0)
	prev := sessionHeld
	sessionHeld = func(lock string) (bool, error) { return filepath.Base(lock) == "feed0002.lock", nil }
	t.Cleanup(func() { sessionHeld = prev })
	for _, tc := range []struct {
		name   string
		agent  catalog.Agent
		resume Resume
		want   string
	}{
		{"daemon flagship", catalog.Agent{ID: catalog.FlagshipID, Transport: catalog.TransportDaemon}, Resume{Pick: true}, "keeps no sessions"},
		{"unknown id", flagship(), Resume{ID: "0000dead"}, "no session"},
		{"open elsewhere", flagship(), Resume{ID: "feed0002"}, "open in another GAIA window"},
	} {
		_, err := resumeTarget(tc.agent, tc.resume)
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Errorf("%s: err = %v, want %q", tc.name, err, tc.want)
		}
	}
}

func TestNoResumeReadsNothing(t *testing.T) {
	got, err := resumeTarget(catalog.Agent{ID: "email", Transport: catalog.TransportDaemon}, Resume{})
	if got != nil || err != nil {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestResumeRefusesAPinnedHistoryFile(t *testing.T) {
	t.Setenv(client.HistoryFileEnv, "/tmp/mine.sqlite3")
	for _, r := range []Resume{{Pick: true}, {ID: "feed0001"}} {
		_, err := resumeTarget(flagship(), r)
		if err == nil || !strings.Contains(err.Error(), client.HistoryFileEnv) {
			t.Errorf("%+v: err = %v", r, err)
		}
	}
}
