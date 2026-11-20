// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiaconnectors

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
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

// fakeCLIStdoutEnv is what the stand-in prints, as the real CLI would.
const fakeCLIStdoutEnv = "GAIACONNECTORS_FAKE_CLI_STDOUT"

// fakeCLISleepEnv is how long the stand-in waits after printing, as the real
// CLI waits on the browser.
const fakeCLISleepEnv = "GAIACONNECTORS_FAKE_CLI_SLEEP"

// fakeCLIBrowserEnv makes the stand-in start a long-lived child that holds its
// stdout, as a browser the CLI opened on Linux does, and write the child's pid
// to the named file.
const fakeCLIBrowserEnv = "GAIACONNECTORS_FAKE_CLI_BROWSER_PID"

func TestMain(m *testing.M) {
	if out := os.Getenv(fakeCLIArgsEnv); out != "" {
		_ = os.WriteFile(out, []byte(strings.Join(os.Args[1:], " ")), 0o600)
		os.Stdout.WriteString(os.Getenv(fakeCLIStdoutEnv))
		if pidFile := os.Getenv(fakeCLIBrowserEnv); pidFile != "" {
			browser := exec.Command("sleep", "20")
			browser.Stdout = os.Stdout
			if browser.Start() == nil {
				_ = os.WriteFile(pidFile, []byte(strconv.Itoa(browser.Process.Pid)), 0o600)
			}
		}
		if d, err := time.ParseDuration(os.Getenv(fakeCLISleepEnv)); err == nil {
			time.Sleep(d)
		}
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

// The shape `gaia connectors list --json` prints once a provider holds
// several accounts (Account.to_dict in src/gaia/connectors/accounts.py).
const multiAccount = `[
 {"id": "google", "display_name": "Google", "type": "oauth_pkce", "configured": true,
  "enabled": true, "account_id": "jane@example.com", "scopes": [], "activations": {},
  "accounts": [
   {"account_id": "1111", "email": "jane@example.com", "status": "ok", "is_default": true},
   {"account_id": "2222", "email": "jane.work@example.com", "status": "reauth_required", "is_default": false},
   {"account_id": "3333", "email": "", "status": "error", "is_default": false}]},
 {"id": "microsoft", "display_name": "Microsoft", "type": "oauth_pkce", "configured": false,
  "enabled": true, "account_id": null, "scopes": [], "activations": {},
  "accounts": [{"account_id": "4444", "email": "jane@example.org", "status": "reauth_required", "is_default": true}]},
 {"id": "github", "display_name": "GitHub", "type": "oauth_pkce", "configured": true,
  "enabled": true, "account_id": "jane@example.net", "scopes": [], "activations": {},
  "accounts": [{"account_id": "5555", "email": "jane@example.net", "status": "ok", "is_default": true}]}]`

func TestRenderListsEachAccountWithItsStateAndTheDefault(t *testing.T) {
	rows, err := Parse([]byte(multiAccount))
	if err != nil {
		t.Fatal(err)
	}
	if len(rows[0].Accounts) != 3 || rows[0].Accounts[1].Status != AccountExpired || !rows[0].Accounts[0].IsDefault {
		t.Fatalf("accounts not parsed: %+v", rows[0].Accounts)
	}
	out := Render(rows)
	for _, want := range []string{
		"Google (google)          connected, 3 accounts",
		"      jane@example.com (default)\n",
		"      jane.work@example.com (sign-in expired: /connectors reconnect google jane.work@example.com)",
		"      3333 (sign-in did not finish: /connectors remove google 3333, then /connectors add google)",
		"Microsoft (microsoft)    needs signing in again",
		"      jane@example.org (default · sign-in expired: /connectors reconnect microsoft jane@example.org)",
		"Another account: /connectors add <id>. Sign one out of GAIA: /connectors remove <id> <address>.",
		"/connectors default <id> <address>",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("render missing %q:\n%s", want, out)
		}
	}
	// One working account reads exactly as before: on the connector's line.
	if !strings.Contains(out, "GitHub (github)          connected as jane@example.net\n") ||
		strings.Count(out, "jane@example.net") != 1 {
		t.Errorf("a single working account must stay on its connector's line:\n%s", out)
	}
	if strings.Contains(Render(rows[2:]), "/connectors default") {
		t.Error("choosing a default means nothing with one account")
	}
}

func TestFindAccountMatchesAddressOrIDIgnoringCase(t *testing.T) {
	rows, _ := Parse([]byte(multiAccount))
	if a, ok := rows[0].FindAccount("JANE.WORK@example.com"); !ok || a.ID != "2222" {
		t.Fatalf("by address: %+v %v", a, ok)
	}
	if a, ok := rows[0].FindAccount("3333"); !ok || a.Label() != "3333" {
		t.Fatalf("by id, labelled by id when there is no address: %+v %v", a, ok)
	}
	if _, ok := rows[0].FindAccount("nobody@example.com"); ok {
		t.Fatal("an unknown account must not match")
	}
}

func TestAccountCommandsRunTheCLI(t *testing.T) {
	google := Connector{ID: "google", DisplayName: "Google", Type: "oauth_pkce"}
	cases := []struct {
		run  func() (string, error)
		want string
	}{
		{func() (string, error) { return Disconnect(context.Background(), google, "jane@example.com") },
			"connectors disconnect google --account jane@example.com"},
		{func() (string, error) { return Disconnect(context.Background(), google, "") },
			"connectors disconnect google"},
		{func() (string, error) { return SetDefault(context.Background(), google, "jane@example.com") },
			"connectors set-default google jane@example.com"},
	}
	for _, tc := range cases {
		argsFile := fakeCLI(t)
		t.Setenv(fakeCLIStdoutEnv, "Removed it.\n")
		report, err := tc.run()
		if err != nil {
			t.Fatal(err)
		}
		got, _ := os.ReadFile(argsFile)
		if string(got) != tc.want || report != "Removed it." {
			t.Errorf("ran %q reporting %q, want %q", got, report, tc.want)
		}
	}
}

func TestSignInHandsBackTheLinkThenTheOutcome(t *testing.T) {
	google := Connector{ID: "google", DisplayName: "Google", Type: "oauth_pkce"}
	for account, want := range map[string]string{
		"":                 "connectors connect google --add",
		"jane@example.com": "connectors connect google --account jane@example.com",
	} {
		argsFile := fakeCLI(t)
		t.Setenv(fakeCLIStdoutEnv, "Open this URL to authorize google:\n  https://accounts.example.com/o?x=1\n"+
			"Sign-in completes via a callback.\nAdded jane@example.com\n")
		s, err := StartSignIn(context.Background(), google, account)
		if err != nil {
			t.Fatal(err)
		}
		if s.URL != "https://accounts.example.com/o?x=1" {
			t.Errorf("link = %q", s.URL)
		}
		report, err := s.Wait()
		if err != nil || report != "Added jane@example.com" {
			t.Errorf("outcome = %q, %v", report, err)
		}
		if got, _ := os.ReadFile(argsFile); string(got) != want {
			t.Errorf("ran %q, want %q", got, want)
		}
	}
}

func TestSignInWithoutALinkFailsLoudly(t *testing.T) {
	fakeCLI(t)
	t.Setenv(fakeCLIStdoutEnv, "nothing useful\n")
	if _, err := StartSignIn(context.Background(), Connector{ID: "google", DisplayName: "Google"}, ""); err == nil ||
		!strings.Contains(err.Error(), "printed no sign-in link") {
		t.Fatalf("want a loud error, got %v", err)
	}
}

const signInText = "Open this URL to authorize google:\n  https://accounts.example.com/o?x=1\nAdded jane@example.com\n"

// waitWithin runs s.Wait, failing the test if it takes longer than limit.
func waitWithin(t *testing.T, s *SignIn, limit time.Duration) (string, error) {
	t.Helper()
	type result struct {
		report string
		err    error
	}
	done := make(chan result, 1)
	go func() {
		report, err := s.Wait()
		done <- result{report, err}
	}()
	select {
	case r := <-done:
		return r.report, r.err
	case <-time.After(limit):
		t.Fatalf("Wait still blocked after %s", limit)
		return "", nil
	}
}

func TestSignInEndsWhenTheCLIExitsThoughTheBrowserHoldsItsOutput(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the stand-in browser is sleep(1)")
	}
	fakeCLI(t)
	pidFile := filepath.Join(t.TempDir(), "browser.pid")
	t.Setenv(fakeCLIStdoutEnv, signInText)
	t.Setenv(fakeCLIBrowserEnv, pidFile)
	t.Cleanup(func() {
		if raw, err := os.ReadFile(pidFile); err == nil {
			if pid, err := strconv.Atoi(string(raw)); err == nil {
				if p, err := os.FindProcess(pid); err == nil {
					_ = p.Kill()
				}
			}
		}
	})
	s, err := StartSignIn(context.Background(), Connector{ID: "google", DisplayName: "Google"}, "")
	if err != nil {
		t.Fatal(err)
	}
	report, err := waitWithin(t, s, 8*time.Second)
	if err != nil || report != "Added jane@example.com" {
		t.Fatalf("outcome = %q, %v", report, err)
	}
}

func TestSignInStopsWhenItsContextIsCancelled(t *testing.T) {
	fakeCLI(t)
	t.Setenv(fakeCLIStdoutEnv, signInText)
	t.Setenv(fakeCLISleepEnv, "30s")
	ctx, cancel := context.WithCancel(context.Background())
	s, err := StartSignIn(ctx, Connector{ID: "google", DisplayName: "Google"}, "")
	if err != nil {
		t.Fatal(err)
	}
	cancel()
	if _, err := waitWithin(t, s, 8*time.Second); err == nil {
		t.Fatal("a cancelled sign-in must end with an error, not a report")
	}
}

func TestAccountChangesOutlastTheListProbesBound(t *testing.T) {
	prevList, prevAccount := ListTimeout, AccountTimeout
	ListTimeout, AccountTimeout = 200*time.Millisecond, 10*time.Second
	t.Cleanup(func() { ListTimeout, AccountTimeout = prevList, prevAccount })
	fakeCLI(t)
	t.Setenv(fakeCLIStdoutEnv, "Removed jane@example.com\n")
	t.Setenv(fakeCLISleepEnv, "600ms")
	google := Connector{ID: "google", DisplayName: "Google", Type: "oauth_pkce"}
	if report, err := Disconnect(context.Background(), google, "jane@example.com"); err != nil ||
		report != "Removed jane@example.com" {
		t.Fatalf("disconnect = %q, %v", report, err)
	}
	if _, err := SetDefault(context.Background(), google, "jane@example.com"); err != nil {
		t.Fatalf("set-default: %v", err)
	}
}
