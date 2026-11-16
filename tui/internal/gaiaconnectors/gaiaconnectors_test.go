// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiaconnectors

import (
	"context"
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

// fakeCLIArgsEnv makes this test binary stand in for `gaia`: TestMain writes
// the argv it was run with to the named file and exits, so a test can check
// the exact command a call would run.
const fakeCLIArgsEnv = "GAIACONNECTORS_FAKE_CLI_ARGS"

func TestMain(m *testing.M) {
	if out := os.Getenv(fakeCLIArgsEnv); out != "" {
		_ = os.WriteFile(out, []byte(strings.Join(os.Args[1:], " ")), 0o600)
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func fakeCLI(t *testing.T) (argsFile string) {
	t.Helper()
	argsFile = filepath.Join(t.TempDir(), "args")
	t.Setenv(fakeCLIArgsEnv, argsFile)
	prev := Binary
	Binary = func() (string, error) { return os.Args[0], nil }
	t.Cleanup(func() { Binary = prev })
	return argsFile
}

const withActivations = `[
 {"id": "mcp-slack", "display_name": "Slack", "type": "mcp_server", "configured": true,
  "enabled": true, "activations": {"installed:gaia": true}},
 {"id": "mcp-notion", "display_name": "Notion", "type": "mcp_server", "configured": true,
  "enabled": true, "activations": {"installed:gaia": false, "builtin:chat": true}},
 {"id": "mcp-jira", "display_name": "Jira", "type": "mcp_server", "configured": true,
  "enabled": false, "activations": {"installed:gaia": true}},
 {"id": "google", "display_name": "Google", "type": "oauth_pkce", "configured": true,
  "activations": {"installed:gaia": true}}]`

func TestRenderShowsTheFlagshipsStandingAccessAndHowToRevokeIt(t *testing.T) {
	rows, err := Parse([]byte(withActivations))
	if err != nil {
		t.Fatal(err)
	}
	out := Render(rows)
	lines := strings.Split(out, "\n")
	lineFor := func(id string) string {
		for _, l := range lines {
			if strings.Contains(l, "("+id+")") {
				return l
			}
		}
		t.Fatalf("no row for %s:\n%s", id, out)
		return ""
	}
	if !strings.Contains(lineFor("mcp-slack"), "GAIA can use it") {
		t.Errorf("an activated server must say GAIA can use it:\n%s", out)
	}
	// Another agent's activation, or an explicit false, is not the flagship's.
	if strings.Contains(lineFor("mcp-notion"), "GAIA can use it") {
		t.Errorf("Notion is not activated for the flagship:\n%s", out)
	}
	// A switched-off server's tools are gone, whatever the ledger says.
	if strings.Contains(lineFor("mcp-jira"), "GAIA can use it") {
		t.Errorf("a switched-off server must not claim GAIA can use it:\n%s", out)
	}
	// OAuth connectors have no activations; a stray ledger entry must not show.
	if strings.Contains(lineFor("google"), "GAIA can use it") {
		t.Errorf("an OAuth row must not claim an activation:\n%s", out)
	}
	if !strings.Contains(out, "/connectors revoke <id> (mcp-slack).") {
		t.Errorf("the revoke command must name exactly the activated server:\n%s", out)
	}
	if !strings.Contains(out, "GAIA keeps access") {
		t.Errorf("the personalize offer must say GAIA keeps access:\n%s", out)
	}
}

func TestRenderWithoutActivationsFromAnOlderCLI(t *testing.T) {
	rows, _ := Parse([]byte(sample))
	if out := Render(rows); strings.Contains(out, "GAIA can use it") || strings.Contains(out, "revoke") {
		t.Fatalf("no activation data must mean no access claim:\n%s", out)
	}
}

func TestDeactivateRunsTheFlagshipDeactivation(t *testing.T) {
	argsFile := fakeCLI(t)
	c := Connector{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server"}
	if err := Deactivate(context.Background(), c); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(argsFile)
	if err != nil {
		t.Fatal(err)
	}
	if want := "connectors activations deactivate mcp-slack installed:gaia"; string(got) != want {
		t.Fatalf("ran %q, want %q", got, want)
	}
}

func TestActivateStillGrantsUse(t *testing.T) {
	argsFile := fakeCLI(t)
	if err := Activate(context.Background(), Connector{ID: "mcp-slack", Type: "mcp_server"}); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(argsFile)
	if want := "connectors activations activate mcp-slack installed:gaia --scopes use"; string(got) != want {
		t.Fatalf("ran %q, want %q", got, want)
	}
}

func TestDeactivateRefusesAnOAuthConnectorWithoutRunningTheCLI(t *testing.T) {
	argsFile := fakeCLI(t)
	err := Deactivate(context.Background(), Connector{ID: "google", DisplayName: "Google", Type: "oauth_pkce"})
	if err == nil || !strings.Contains(err.Error(), "Disconnect it") {
		t.Fatalf("want an actionable refusal, got %v", err)
	}
	if _, statErr := os.Stat(argsFile); !os.IsNotExist(statErr) {
		t.Fatal("the CLI must not run for an OAuth connector")
	}
}
