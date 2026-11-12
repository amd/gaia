// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiaconnectors

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sample = `INFO loading catalog
[{"id": "google", "display_name": "Google", "type": "oauth_pkce", "category": "productivity",
  "tier": "core", "configured": true, "account_id": "me@example.com", "scopes": []},
 {"id": "slack", "display_name": "Slack", "type": "mcp_server", "category": "chat",
  "tier": "core", "configured": false, "account_id": null, "scopes": []}]`

func TestParseSkipsLogLinesAndReadsRows(t *testing.T) {
	rows, err := Parse([]byte(sample))
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 || !rows[0].Configured || rows[0].AccountID != "me@example.com" || rows[1].Configured {
		t.Fatalf("unexpected rows: %+v", rows)
	}
}

func TestParseRejectsNonJSON(t *testing.T) {
	if _, err := Parse([]byte("usage: gaia ...")); err == nil {
		t.Fatal("want an error for non-JSON output")
	}
}

func TestRenderOffersPersonalizeOnlyWhenConnected(t *testing.T) {
	rows, _ := Parse([]byte(sample))
	out := Render(rows)
	if !strings.Contains(out, "connected as me@example.com") || !strings.Contains(out, "/connectors personalize") {
		t.Fatalf("render missing status or offer:\n%s", out)
	}
	if strings.Contains(Render(rows[1:]), "personalize") {
		t.Fatal("offer shown with nothing connected")
	}
}

func TestFindAndPrompt(t *testing.T) {
	rows, _ := Parse([]byte(sample))
	c, ok := Find(rows, "GOOGLE")
	if !ok {
		t.Fatal("Find is not case-insensitive")
	}
	p := PersonalizePrompt(c)
	for _, want := range []string{"I just connected Google", "Gmail", "Read only", "memory", "Don't create any schedules yet"} {
		if !strings.Contains(p, want) {
			t.Errorf("prompt missing %q", want)
		}
	}
	if got := Source(Connector{ID: "mcp-slack", DisplayName: "Slack"}); !strings.Contains(got, "channels") {
		t.Errorf("mcp-slack source = %q", got)
	}
	if got := Source(Connector{ID: "mcp-notion", DisplayName: "Notion"}); got != "my recent activity in Notion" {
		t.Errorf("fallback source = %q", got)
	}
}

// The Agent UI builds the same prompt in TypeScript; this keeps the two from
// drifting apart.
func TestPromptMatchesTheAgentUI(t *testing.T) {
	path := filepath.Join("..", "..", "..", "src", "gaia", "apps", "webui", "src", "utils", "connectorPersonalize.ts")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	ts := strings.ReplaceAll(string(raw), "\\'", "'")
	for _, s := range sources {
		if !strings.Contains(ts, s.what) {
			t.Errorf("source %q is missing from %s", s.what, path)
		}
	}
	lines := strings.Split(PersonalizePrompt(Connector{ID: "x", DisplayName: "X"}), "\n")
	for _, line := range lines[1:] {
		if !strings.Contains(ts, line) {
			t.Errorf("prompt line %q is missing from %s", line, path)
		}
	}
}

func TestRenderMarksSwitchedOffConnectors(t *testing.T) {
	off := false
	out := Render([]Connector{{ID: "mcp-notion", DisplayName: "Notion", Configured: true, Enabled: &off}})
	if !strings.Contains(out, "switched off") || strings.Contains(out, "personalize") {
		t.Fatalf("switched-off connector rendered wrong:\n%s", out)
	}
}
