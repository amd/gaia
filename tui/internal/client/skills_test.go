// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestSSEListSkillsReadsTheAgentsList(t *testing.T) {
	f := newFakeRelay(t)
	// The shape skills_listing() answers: more fields than the TUI reads.
	f.skillsBody = `{"skills":[` +
		`{"name":"daily-brief","description":"A short brief.","version":"1.0","tier":"safe","tools":[],"path":"agent-bundled","always_on":false,"source":"agent-bundled"},` +
		`{"name":"browser-pane","description":"Drive the pane.","source":"agent-bundled"},` +
		`{"name":"file-ops","description":"","version":null,"tier":"safe","tools":["list_dir"],"path":"user","always_on":false,"source":"user"}` +
		`],"invalid":[]}`
	c := f.clientFor(t, "gaia")
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	got, err := c.ListSkills(ctx)
	if err != nil {
		t.Fatalf("ListSkills: %v", err)
	}
	// browser-pane needs the Agent UI's pane, so a terminal chat is not offered it.
	if len(got) != 2 || got[0].Name != "daily-brief" || got[0].Description != "A short brief." || got[1].Name != "file-ops" {
		t.Errorf("skills = %+v", got)
	}
}

func TestSSEListSkillsTellsNoSkillsFromNoAnswer(t *testing.T) {
	f := newFakeRelay(t)
	f.skillsBody = `{"skills":[],"invalid":[]}`
	c := f.clientFor(t, "gaia")
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	got, err := c.ListSkills(ctx)
	if err != nil || got == nil || len(got) != 0 {
		t.Errorf("an agent with no skills: %+v, %v; want an empty, non-nil list", got, err)
	}

	// An agent with no such route is an error, never an empty list.
	f.skillsStatus = http.StatusNotFound
	got, err = c.ListSkills(ctx)
	if err == nil || got != nil {
		t.Fatalf("a 404 returned %+v, %v", got, err)
	}
	if !strings.Contains(err.Error(), "skill list") || !strings.Contains(err.Error(), "Not Found") {
		t.Errorf("the error does not say what was refused: %v", err)
	}
}
