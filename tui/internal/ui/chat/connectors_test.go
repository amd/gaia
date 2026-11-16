// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/gaiaconnectors"
)

var testConnectors = []gaiaconnectors.Connector{
	{ID: "google", DisplayName: "Google", Configured: true, AccountID: "me@example.com"},
	{ID: "mcp-slack", DisplayName: "Slack"},
}

func TestConnectorsCommandListsConnectors(t *testing.T) {
	m := slackModel(t)
	updated, cmd := m.submit("/connectors")
	if cmd == nil {
		t.Fatal("/connectors must start the CLI probe")
	}
	updated, _ = updated.(ChatModel).handleConnectorsList(connectorsListMsg{rows: testConnectors})
	text := lastStatus(t, updated.(ChatModel))
	for _, want := range []string{"Google", "connected as me@example.com", "Slack", "not connected", "/connectors personalize"} {
		if !strings.Contains(text, want) {
			t.Errorf("listing missing %q:\n%s", want, text)
		}
	}
}

func TestPersonalizeActivatesThenSendsThePrompt(t *testing.T) {
	m := slackModel(t)
	before := len(m.messages)
	updated, cmd := m.handleConnectorsList(connectorsListMsg{rows: testConnectors, req: connectorsRequest{verb: connectorsPersonalize, arg: "google"}})
	if cmd == nil || len(updated.(ChatModel).messages) != before {
		t.Fatal("the prompt must wait for activation, not go out with the list")
	}
	sent, _ := updated.(ChatModel).handleConnectorsActivated(connectorsActivatedMsg{connector: testConnectors[0]})
	msgs := sent.(ChatModel).messages
	last := msgs[len(msgs)-1]
	if last.Role != RoleUser || !strings.Contains(last.Content, "I just connected Google") {
		t.Fatalf("want the personalization prompt sent as a user turn, got %+v", last)
	}
}

func TestPersonalizeOfAToolServerSaysAccessStaysAndHowToRevoke(t *testing.T) {
	m := slackModel(t)
	slack := gaiaconnectors.Connector{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server", Configured: true}
	sent, _ := m.handleConnectorsActivated(connectorsActivatedMsg{connector: slack})
	if text := lastStatus(t, sent.(ChatModel)); !strings.Contains(text, "keeps that access") ||
		!strings.Contains(text, "/connectors revoke mcp-slack") {
		t.Fatalf("the user must be told GAIA keeps access and how to turn it off: %s", text)
	}
	msgs := sent.(ChatModel).messages
	if last := msgs[len(msgs)-1]; last.Role != RoleUser {
		t.Fatal("the pass must still go out after the notice")
	}
	// An OAuth pass activates nothing, so there is nothing new to disclose.
	plain, _ := slackModel(t).handleConnectorsActivated(connectorsActivatedMsg{connector: testConnectors[0]})
	for _, msg := range plain.(ChatModel).messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "revoke") {
			t.Fatalf("no revoke notice for an OAuth connector: %s", msg.Content)
		}
	}
}

func TestPersonalizeWaitsForARunningTurn(t *testing.T) {
	m := slackModel(t)
	m.streaming = true
	before := len(m.messages)
	updated, cmd := m.handleConnectorsActivated(connectorsActivatedMsg{connector: testConnectors[0]})
	if cmd == nil || len(updated.(ChatModel).messages) != before {
		t.Fatal("a pass must not start a second turn over a running one")
	}
}

func TestPersonalizeReportsAFailedActivation(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleConnectorsActivated(connectorsActivatedMsg{
		connector: testConnectors[1], err: errors.New("keyring locked"),
	})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "keyring locked") {
		t.Errorf("activation failure must be shown: %s", text)
	}
}

func TestPersonalizeRefusesASwitchedOffConnector(t *testing.T) {
	off := false
	rows := []gaiaconnectors.Connector{{ID: "mcp-notion", DisplayName: "Notion", Configured: true, Enabled: &off}}
	m := slackModel(t)
	updated, cmd := m.handleConnectorsList(connectorsListMsg{rows: rows, req: connectorsRequest{verb: connectorsPersonalize, arg: "mcp-notion"}})
	if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "switched off") {
		t.Fatal("a switched-off connector must be refused before activation")
	}
}

func TestPersonalizeRefusesUnconnectedAndUnknown(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleConnectorsList(connectorsListMsg{rows: testConnectors, req: connectorsRequest{verb: connectorsPersonalize, arg: "mcp-slack"}})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "isn't connected yet") {
		t.Errorf("unconnected: %s", text)
	}
	updated, _ = m.handleConnectorsList(connectorsListMsg{rows: testConnectors, req: connectorsRequest{verb: connectorsPersonalize, arg: "nope"}})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "No connector named") {
		t.Errorf("unknown: %s", text)
	}
	updated, cmd := m.submit("/connectors personalize")
	if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "Name a connected connector") {
		t.Error("bare personalize must ask for an id without probing")
	}
}

func TestStatusBarConnectorsEntryIsClickable(t *testing.T) {
	m := slackModel(t)
	m.width, m.height = 120, 30
	plain := ansi.Strip(m.renderStatusBar())
	col := strings.Index(plain, connectorsHint)
	if col < 0 {
		t.Fatalf("idle status bar should show %q: %s", connectorsHint, plain)
	}
	x := ansi.StringWidth(plain[:col]) + 1
	press := tea.MouseMsg{X: x, Y: m.height - 1, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress}
	release := press
	release.Action = tea.MouseActionRelease
	pressed, _ := m.handleTranscriptMouse(press)
	clicked, cmd := pressed.(ChatModel).handleTranscriptMouse(release)
	if cmd == nil || !strings.Contains(lastStatus(t, clicked.(ChatModel)), "Checking connectors") {
		t.Fatal("a real press and release on the entry must open connectors")
	}
	if m.clickedConnectorsHint(0, m.height-1) || m.clickedConnectorsHint(ansi.StringWidth(plain[:col])+1, 2) {
		t.Fatal("clicks elsewhere must not open connectors")
	}
	m.streaming = true
	if strings.Contains(ansi.Strip(m.renderStatusBar()), connectorsHint) {
		t.Fatal("the entry must give way while a turn runs")
	}
}

var revokeRows = []gaiaconnectors.Connector{
	{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server", Configured: true,
		Activations: map[string]bool{gaiaconnectors.FlagshipAgentID: true}},
	{ID: "mcp-notion", DisplayName: "Notion", Type: "mcp_server", Configured: true,
		Activations: map[string]bool{"builtin:chat": true}},
	{ID: "google", DisplayName: "Google", Type: "oauth_pkce", Configured: true},
}

func stubActivate(t *testing.T) *[]string {
	t.Helper()
	var calls []string
	prev := activateConnectorCmd
	activateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
		calls = append(calls, c.ID)
		return func() tea.Msg { return connectorsActivatedMsg{connector: c} }
	}
	t.Cleanup(func() { activateConnectorCmd = prev })
	return &calls
}

func stubDeactivate(t *testing.T) *[]string {
	t.Helper()
	var calls []string
	prev := deactivateConnectorCmd
	deactivateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
		calls = append(calls, c.ID)
		return func() tea.Msg { return connectorsRevokedMsg{connector: c} }
	}
	t.Cleanup(func() { deactivateConnectorCmd = prev })
	return &calls
}

func TestRevokeTurnsOffTheFlagshipsAccess(t *testing.T) {
	calls := stubDeactivate(t)
	m := slackModel(t)
	before := len(m.messages)
	updated, cmd := m.submit("/connectors revoke Slack")
	if cmd == nil {
		t.Fatal("/connectors revoke must look the connector up")
	}
	for _, msg := range updated.(ChatModel).messages[before:] {
		if msg.Role == RoleUser {
			t.Fatal("/connectors revoke must not go to the agent as a question")
		}
	}
	updated, cmd = updated.(ChatModel).handleConnectorsList(connectorsListMsg{rows: revokeRows, req: connectorsRequest{verb: connectorsRevoke, arg: "Slack"}})
	if cmd == nil || len(*calls) != 1 || (*calls)[0] != "mcp-slack" {
		t.Fatalf("want one deactivation of mcp-slack, got %v", *calls)
	}
	done, _ := updated.(ChatModel).handleConnectorsRevoked(cmd().(connectorsRevokedMsg))
	if text := lastStatus(t, done.(ChatModel)); !strings.Contains(text, "can no longer use Slack") {
		t.Fatalf("revoke must confirm: %s", text)
	}
}

func TestRevokeRefusesWhatItCannotOrNeedNotTurnOff(t *testing.T) {
	calls := stubDeactivate(t)
	m := slackModel(t)
	cases := map[string]string{
		"mcp-notion":     "already can't use Notion", // another agent's activation is not GAIA's
		"nope":           "No connector named",
		"mcp-slack work": "has no separate accounts",
	}
	for name, want := range cases {
		updated, cmd := m.handleConnectorsList(connectorsListMsg{rows: revokeRows, req: connectorsRequest{verb: connectorsRevoke, arg: name}})
		if cmd != nil {
			t.Errorf("%s: must not run a deactivation", name)
		}
		if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, want) {
			t.Errorf("%s: want %q in %q", name, want, text)
		}
	}
	updated, cmd := m.submit("/connectors revoke")
	if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "/connectors revoke <id>") {
		t.Error("bare revoke must ask for an id without probing")
	}
	// "/connectors revokeX" is not the command.
	if _, ok := parseConnectorsCommand("/connectors revokeall"); ok {
		t.Error("a longer word must not match the revoke prefix")
	}
	if len(*calls) != 0 {
		t.Fatalf("no deactivation should have run, got %v", *calls)
	}
}

func TestRevokeStillRunsWhenAnOlderCLIReportsNoActivations(t *testing.T) {
	calls := stubDeactivate(t)
	rows := []gaiaconnectors.Connector{{ID: "mcp-slack", DisplayName: "Slack", Type: "mcp_server", Configured: true}}
	_, cmd := slackModel(t).handleConnectorsList(connectorsListMsg{rows: rows, req: connectorsRequest{verb: connectorsRevoke, arg: "mcp-slack"}})
	if cmd == nil || len(*calls) != 1 {
		t.Fatal("without activation data the revoke must still run, not claim GAIA has no access")
	}
}

func TestRevokeReportsAFailedDeactivation(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleConnectorsRevoked(connectorsRevokedMsg{
		connector: revokeRows[0], err: errors.New("ledger locked"),
	})
	if text := lastStatus(t, updated.(ChatModel)); !strings.Contains(text, "ledger locked") {
		t.Errorf("deactivation failure must be shown: %s", text)
	}
}

var googleAccounts = gaiaconnectors.Connector{
	ID: "google", DisplayName: "Google", Type: "oauth_pkce", Configured: true, AccountID: "jane@example.com",
	Accounts: []gaiaconnectors.Account{
		{ID: "1111", Email: "jane@example.com", Status: gaiaconnectors.AccountOK, IsDefault: true},
		{ID: "2222", Email: "jane.work@example.com", Status: gaiaconnectors.AccountExpired},
	},
}

// stubAccountCmds records the CLI work each account command would start.
func stubAccountCmds(t *testing.T) *[]string {
	t.Helper()
	var calls []string
	prevDisc, prevDef, prevSign := disconnectAccountCmd, setDefaultAccountCmd, startSignInCmd
	disconnectAccountCmd = func(c gaiaconnectors.Connector, account string) tea.Cmd {
		calls = append(calls, "disconnect "+c.ID+" "+account)
		return func() tea.Msg { return connectorsReportMsg{report: "Removed " + account} }
	}
	setDefaultAccountCmd = func(c gaiaconnectors.Connector, account string) tea.Cmd {
		calls = append(calls, "default "+c.ID+" "+account)
		return func() tea.Msg { return connectorsReportMsg{} }
	}
	startSignInCmd = func(_ context.Context, c gaiaconnectors.Connector, account string) tea.Cmd {
		calls = append(calls, "sign-in "+c.ID+" "+account)
		return func() tea.Msg { return connectorsSignInMsg{connector: c} }
	}
	t.Cleanup(func() { disconnectAccountCmd, setDefaultAccountCmd, startSignInCmd = prevDisc, prevDef, prevSign })
	return &calls
}

func runConnectorsCommand(t *testing.T, rows []gaiaconnectors.Connector, query string) (string, tea.Cmd) {
	t.Helper()
	req, ok := parseConnectorsCommand(query)
	if !ok {
		t.Fatalf("%q is not a connectors command", query)
	}
	updated, cmd := slackModel(t).handleConnectorsList(connectorsListMsg{rows: rows, req: req})
	return lastStatus(t, updated.(ChatModel)), cmd
}

func TestAccountCommandsActOnTheNamedAccount(t *testing.T) {
	rows := []gaiaconnectors.Connector{googleAccounts}
	cases := map[string]string{
		"/connectors remove google jane.work@example.com":    "disconnect google jane.work@example.com",
		"/connectors remove Google JANE.WORK@example.com":    "disconnect google jane.work@example.com",
		"/connectors remove google 2222":                     "disconnect google jane.work@example.com",
		"/connectors add google":                             "sign-in google ",
		"/connectors reconnect google jane.work@example.com": "sign-in google jane.work@example.com",
		"/connectors default google jane.work@example.com":   "default google jane.work@example.com",
	}
	for query, want := range cases {
		calls := stubAccountCmds(t)
		if _, cmd := runConnectorsCommand(t, rows, query); cmd == nil || len(*calls) != 1 || (*calls)[0] != want {
			t.Errorf("%s: ran %v, want [%s]", query, *calls, want)
		}
	}
}

func TestAccountCommandsWithSeveralAccountsAndNoneNamedShowTheChoices(t *testing.T) {
	calls := stubAccountCmds(t)
	rows := []gaiaconnectors.Connector{googleAccounts}
	for _, verb := range []string{"remove", "reconnect", "default"} {
		text, cmd := runConnectorsCommand(t, rows, "/connectors "+verb+" google")
		if cmd != nil {
			t.Errorf("%s: must not act without a chosen account", verb)
		}
		for _, want := range []string{"2 accounts", "jane@example.com, jane.work@example.com", "/connectors " + verb + " google <address>"} {
			if !strings.Contains(text, want) {
				t.Errorf("%s: want %q in %q", verb, want, text)
			}
		}
	}
	text, _ := runConnectorsCommand(t, rows, "/connectors remove google nobody@example.com")
	if !strings.Contains(text, `no account "nobody@example.com"`) || !strings.Contains(text, "jane.work@example.com") {
		t.Errorf("an unknown account must list the real ones: %s", text)
	}
	if text, _ := runConnectorsCommand(t, rows, "/connectors default google jane@example.com"); !strings.Contains(text, "already the default") {
		t.Errorf("the default needs no change: %s", text)
	}
	if len(*calls) != 0 {
		t.Fatalf("nothing should have run, got %v", *calls)
	}
}

func TestRemoveOfASingleAccountDisconnectsIt(t *testing.T) {
	one := googleAccounts
	one.Accounts = one.Accounts[:1]
	// An older CLI lists no accounts; plain disconnect works there too.
	older := gaiaconnectors.Connector{ID: "google", DisplayName: "Google", Type: "oauth_pkce", Configured: true}
	for name, rows := range map[string][]gaiaconnectors.Connector{"one": {one}, "older": {older}} {
		calls := stubAccountCmds(t)
		_, cmd := runConnectorsCommand(t, rows, "/connectors remove google")
		want := "disconnect google jane@example.com"
		if name == "older" {
			want = "disconnect google "
		}
		if cmd == nil || len(*calls) != 1 || (*calls)[0] != want {
			t.Errorf("%s: ran %v, want [%s]", name, *calls, want)
		}
	}
	calls := stubAccountCmds(t)
	text, _ := runConnectorsCommand(t, []gaiaconnectors.Connector{older}, "/connectors remove google jane@example.com")
	if !strings.Contains(text, "one at a time") || len(*calls) != 0 {
		t.Errorf("an older CLI cannot take --account: %s %v", text, *calls)
	}
}

func TestAccountCommandsRefuseAToolServer(t *testing.T) {
	calls := stubAccountCmds(t)
	for _, query := range []string{"/connectors add Slack", "/connectors default mcp-slack"} {
		if text, cmd := runConnectorsCommand(t, revokeRows, query); cmd != nil || !strings.Contains(text, "Agent UI") {
			t.Errorf("%s: want a pointer to the Agent UI, got %q", query, text)
		}
	}
	if len(*calls) != 0 {
		t.Fatalf("nothing should have run, got %v", *calls)
	}
}

func TestBareAccountCommandsAskWithoutProbing(t *testing.T) {
	for _, verb := range []string{"add", "remove", "reconnect", "default"} {
		updated, cmd := slackModel(t).submit("/connectors " + verb)
		if cmd != nil || !strings.Contains(lastStatus(t, updated.(ChatModel)), "/connectors "+verb+" <id>") {
			t.Errorf("bare %s must ask for an id without probing", verb)
		}
	}
}

func TestSignInShowsTheLinkThenTheOutcome(t *testing.T) {
	m := slackModel(t)
	prev := waitSignInCmd
	waitSignInCmd = func(*gaiaconnectors.SignIn) tea.Cmd {
		return func() tea.Msg { return connectorsReportMsg{report: "Added jane.work@example.com"} }
	}
	t.Cleanup(func() { waitSignInCmd = prev })
	started, cmd := m.handleConnectorsSignIn(connectorsSignInMsg{
		connector: googleAccounts, signIn: &gaiaconnectors.SignIn{URL: "https://accounts.example.com/o"},
	})
	if text := lastStatus(t, started.(ChatModel)); cmd == nil || !strings.Contains(text, "https://accounts.example.com/o") {
		t.Fatalf("the link must be shown while GAIA waits: %s", text)
	}
	done, _ := started.(ChatModel).handleConnectorsReport(cmd().(connectorsReportMsg))
	if text := lastStatus(t, done.(ChatModel)); text != "Added jane.work@example.com" {
		t.Fatalf("the outcome must be shown: %s", text)
	}
	failed, _ := m.handleConnectorsReport(connectorsReportMsg{err: errors.New("exit status 5: pick one")})
	if text := lastStatus(t, failed.(ChatModel)); !strings.Contains(text, "pick one") {
		t.Fatalf("a failure must be shown: %s", text)
	}
}

func TestRevokeOnAnAccountConnectorPointsToRemoveAndSignsNothingOut(t *testing.T) {
	calls := stubAccountCmds(t)
	rows := []gaiaconnectors.Connector{googleAccounts}
	for _, query := range []string{"/connectors revoke google", "/connectors revoke google jane.work@example.com"} {
		text, cmd := runConnectorsCommand(t, rows, query)
		if cmd != nil || !strings.Contains(text, "/connectors remove google") {
			t.Errorf("%s: want a pointer to /connectors remove, got %q", query, text)
		}
	}
	if len(*calls) != 0 {
		t.Fatalf("revoke must not sign an account out, ran %v", *calls)
	}
}

func TestRemoveRefusesAToolServerAndPointsToRevoke(t *testing.T) {
	calls := stubAccountCmds(t)
	deactivations := stubDeactivate(t)
	text, cmd := runConnectorsCommand(t, revokeRows, "/connectors remove mcp-slack")
	if cmd != nil || !strings.Contains(text, "/connectors revoke mcp-slack") {
		t.Errorf("want a pointer to revoke, got %q", text)
	}
	if len(*calls) != 0 || len(*deactivations) != 0 {
		t.Fatalf("nothing should have run, got %v %v", *calls, *deactivations)
	}
}

func TestPersonalizeRefusesAnAccount(t *testing.T) {
	calls := stubActivate(t)
	text, cmd := runConnectorsCommand(t, []gaiaconnectors.Connector{googleAccounts},
		"/connectors personalize google jane.work@example.com")
	if cmd != nil || !strings.Contains(text, "/connectors personalize google") {
		t.Errorf("an account must be refused, not ignored: %q", text)
	}
	if len(*calls) != 0 {
		t.Fatalf("nothing should have been activated, got %v", *calls)
	}
}

func TestAccountCommandsOnAnOlderCLIAskForAnUpdate(t *testing.T) {
	older := []gaiaconnectors.Connector{{ID: "google", DisplayName: "Google", Type: "oauth_pkce", Configured: true}}
	calls := stubAccountCmds(t)
	for _, query := range []string{
		"/connectors add google",
		"/connectors reconnect google jane@example.com",
		"/connectors default google jane@example.com",
		"/connectors remove google jane@example.com",
	} {
		text, cmd := runConnectorsCommand(t, older, query)
		if cmd != nil || !strings.Contains(text, "Update GAIA") || strings.Contains(text, "/connectors add") {
			t.Errorf("%s: want an update message, got %q", query, text)
		}
	}
	if len(*calls) != 0 {
		t.Fatalf("nothing should have run, got %v", *calls)
	}
	none := googleAccounts
	none.Configured, none.Accounts = false, []gaiaconnectors.Account{}
	if text, _ := runConnectorsCommand(t, []gaiaconnectors.Connector{none}, "/connectors default google"); !strings.Contains(text, "/connectors add google") {
		t.Errorf("a current CLI with no accounts should offer to add one: %q", text)
	}
}

func TestReconnectThatSignedInAnotherAccountShowsTheCLIsWords(t *testing.T) {
	report := "Signed in as jane.other@example.com, which is a different account from jane.work@example.com, " +
		"so it was added as a new account; jane.work@example.com still needs reconnecting"
	done, _ := slackModel(t).handleConnectorsReport(connectorsReportMsg{report: report})
	if text := lastStatus(t, done.(ChatModel)); text != report {
		t.Fatalf("the CLI's explanation must reach the screen as written: %q", text)
	}
}

func TestLeavingTheChatStopsAPendingSignIn(t *testing.T) {
	var ctxs []context.Context
	prev := startSignInCmd
	startSignInCmd = func(ctx context.Context, _ gaiaconnectors.Connector, _ string) tea.Cmd {
		ctxs = append(ctxs, ctx)
		return func() tea.Msg { return nil }
	}
	t.Cleanup(func() { startSignInCmd = prev })
	m := slackModel(t)
	for _, query := range []string{"/connectors add google", "/connectors reconnect google jane.work@example.com"} {
		req, _ := parseConnectorsCommand(query)
		updated, _ := m.handleConnectorsList(connectorsListMsg{rows: []gaiaconnectors.Connector{googleAccounts}, req: req})
		m = updated.(ChatModel)
	}
	if len(ctxs) != 2 || ctxs[0].Err() != nil || ctxs[1].Err() != nil {
		t.Fatalf("both sign-ins should be running: %v", ctxs)
	}
	m.Retire()
	for i, ctx := range ctxs {
		if ctx.Err() == nil {
			t.Errorf("sign-in %d still running after the chat retired", i)
		}
	}
}
