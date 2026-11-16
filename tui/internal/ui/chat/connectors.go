// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/x/ansi"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiaconnectors"
)

// connectorsVerb is the word after /connectors; "" lists the connectors.
type connectorsVerb string

const (
	connectorsShow        connectorsVerb = ""
	connectorsPersonalize connectorsVerb = "personalize"
	connectorsRevoke      connectorsVerb = "revoke"
	connectorsRemove      connectorsVerb = "remove"
	connectorsAdd         connectorsVerb = "add"
	connectorsReconnect   connectorsVerb = "reconnect"
	connectorsDefault     connectorsVerb = "default"
)

// connectorsAsk is what each command says when typed with nothing after it.
var connectorsAsk = map[connectorsVerb]string{
	connectorsPersonalize: "Name a connected connector: /connectors personalize <id>. " +
		"Type /connectors to see their ids.",
	connectorsRevoke: "Name the connector to turn off GAIA's access to: /connectors revoke <id>. " +
		"Type /connectors to see which ones GAIA can use.",
	connectorsRemove: "Name the account to sign out of GAIA: /connectors remove <id> <address>. " +
		"Type /connectors to see them.",
	connectorsAdd: "Name the connector to add an account to: /connectors add <id>. " +
		"Type /connectors to see their ids.",
	connectorsReconnect: "Name the account to sign in again: /connectors reconnect <id> <address>. " +
		"Type /connectors to see them.",
	connectorsDefault: "Name the account GAIA should use when you don't say: " +
		"/connectors default <id> <address>. Type /connectors to see them.",
}

// connectorsRequest is a parsed /connectors command. arg is everything after
// the word: a connector, maybe followed by an account. It is split once the
// list is in, because a display name can contain spaces.
type connectorsRequest struct {
	verb connectorsVerb
	arg  string
}

// parseConnectorsCommand reads `/connectors <word> [arg]`.
func parseConnectorsCommand(query string) (connectorsRequest, bool) {
	rest, ok := strings.CutPrefix(query, "/connectors ")
	if !ok {
		return connectorsRequest{}, false
	}
	word, arg, _ := strings.Cut(strings.TrimSpace(rest), " ")
	verb := connectorsVerb(word)
	if _, ok := connectorsAsk[verb]; !ok {
		return connectorsRequest{}, false
	}
	return connectorsRequest{verb: verb, arg: strings.TrimSpace(arg)}, true
}

// connectorsActivatedMsg reports that the flagship may now use the connector
// a personalization pass is about to read.
type connectorsActivatedMsg struct {
	connector gaiaconnectors.Connector
	err       error
}

// activateConnectorCmd runs the CLI activation off the UI goroutine.
var activateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
	return func() tea.Msg {
		return connectorsActivatedMsg{connector: c, err: gaiaconnectors.Activate(context.Background(), c)}
	}
}

// connectorsRevokedMsg reports that the flagship can no longer use a connector.
type connectorsRevokedMsg struct {
	connector gaiaconnectors.Connector
	err       error
}

// deactivateConnectorCmd runs the CLI deactivation off the UI goroutine.
var deactivateConnectorCmd = func(c gaiaconnectors.Connector) tea.Cmd {
	return func() tea.Msg {
		return connectorsRevokedMsg{connector: c, err: gaiaconnectors.Deactivate(context.Background(), c)}
	}
}

// connectorsReportMsg is what an account change printed, or why it failed.
type connectorsReportMsg struct {
	report string
	err    error
}

// disconnectAccountCmd removes one account, or the only one when account is "".
var disconnectAccountCmd = func(c gaiaconnectors.Connector, account string) tea.Cmd {
	return func() tea.Msg {
		report, err := gaiaconnectors.Disconnect(context.Background(), c, account)
		return connectorsReportMsg{report: report, err: err}
	}
}

// setDefaultAccountCmd makes account the one the connector uses by default.
var setDefaultAccountCmd = func(c gaiaconnectors.Connector, account string) tea.Cmd {
	return func() tea.Msg {
		report, err := gaiaconnectors.SetDefault(context.Background(), c, account)
		return connectorsReportMsg{report: report, err: err}
	}
}

// connectorsSignInMsg reports that a browser sign-in has started.
type connectorsSignInMsg struct {
	connector gaiaconnectors.Connector
	signIn    *gaiaconnectors.SignIn
	err       error
}

// startSignInCmd adds an account (account == "") or signs one in again.
var startSignInCmd = func(ctx context.Context, c gaiaconnectors.Connector, account string) tea.Cmd {
	return func() tea.Msg {
		s, err := gaiaconnectors.StartSignIn(ctx, c, account)
		return connectorsSignInMsg{connector: c, signIn: s, err: err}
	}
}

// waitSignInCmd waits out the browser sign-in, up to the CLI's 15 minutes.
var waitSignInCmd = func(s *gaiaconnectors.SignIn) tea.Cmd {
	return func() tea.Msg {
		report, err := s.Wait()
		return connectorsReportMsg{report: report, err: err}
	}
}

// connectorsListMsg carries `gaia connectors list --json`, and the command
// the list was fetched for.
type connectorsListMsg struct {
	rows []gaiaconnectors.Connector
	err  error
	req  connectorsRequest
}

// listConnectorsCmd runs the CLI probe off the UI goroutine.
var listConnectorsCmd = func(req connectorsRequest) tea.Cmd {
	return func() tea.Msg {
		rows, err := gaiaconnectors.List(context.Background())
		return connectorsListMsg{rows: rows, err: err, req: req}
	}
}

func (m ChatModel) startConnectorsList() (tea.Model, tea.Cmd) {
	return m.statusNote("Checking connectors…"), listConnectorsCmd(connectorsRequest{})
}

func (m ChatModel) startConnectorsCommand(req connectorsRequest) (tea.Model, tea.Cmd) {
	if req.arg == "" {
		return m.statusNote(connectorsAsk[req.verb]), nil
	}
	return m.statusNote("Checking " + req.arg + "…"), listConnectorsCmd(req)
}

// findConnector resolves arg to a connector and an optional account: the
// whole of arg names the connector, or its last word is the account.
func findConnector(rows []gaiaconnectors.Connector, arg string) (gaiaconnectors.Connector, string, bool) {
	if c, ok := gaiaconnectors.Find(rows, arg); ok {
		return c, "", true
	}
	if i := strings.LastIndex(arg, " "); i > 0 {
		if c, ok := gaiaconnectors.Find(rows, strings.TrimSpace(arg[:i])); ok {
			return c, arg[i+1:], true
		}
	}
	return gaiaconnectors.Connector{}, "", false
}

func (m ChatModel) handleConnectorsList(msg connectorsListMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf("Could not list connectors: %v", msg.err)), nil
	}
	if msg.req.verb == connectorsShow {
		return m.statusNote(gaiaconnectors.Render(msg.rows)), nil
	}
	c, account, ok := findConnector(msg.rows, msg.req.arg)
	if !ok {
		return m.statusNote(fmt.Sprintf("No connector named %q. Type /connectors to see their ids.",
			msg.req.arg)), nil
	}
	if account != "" && c.Type != "oauth_pkce" {
		return m.statusNote(fmt.Sprintf("%s has no separate accounts. Type /connectors to see them.",
			c.DisplayName)), nil
	}
	switch msg.req.verb {
	case connectorsPersonalize:
		if account != "" {
			return m.statusNote(fmt.Sprintf("Personalize reads all of %s, not one account: "+
				"/connectors personalize %s", c.DisplayName, c.ID)), nil
		}
		return m.personalizeConnector(c)
	case connectorsRevoke:
		switch c.Type {
		case "oauth_pkce":
			return m.statusNote(fmt.Sprintf("GAIA's access to %s comes with signing in. To sign an "+
				"account out of GAIA: /connectors remove %s <address>", c.DisplayName, c.ID)), nil
		case "mcp_server":
			return m.revokeActivation(c)
		}
		return m.statusNote(fmt.Sprintf("GAIA's access to %s comes with connecting it. Disconnect it "+
			"under Connectors in the Agent UI to remove that.", c.DisplayName)), nil
	}
	if c.Type != "oauth_pkce" {
		hint := "Set it up from Connectors in the Agent UI."
		if msg.req.verb == connectorsRemove && c.Type == "mcp_server" {
			hint = "To turn off GAIA's use of it: /connectors revoke " + c.ID
		}
		return m.statusNote(fmt.Sprintf("%s has no accounts to manage here. %s", c.DisplayName, hint)), nil
	}
	if msg.req.verb == connectorsRemove {
		return m.removeAccount(c, account)
	}
	if c.Accounts == nil {
		return m.statusNote(olderCLINote(c)), nil
	}
	if msg.req.verb == connectorsAdd {
		return m.startSignIn(c, "", "Opening "+c.DisplayName+" sign-in…")
	}
	a, refusal := pickAccount(c, account, msg.req.verb)
	if refusal != "" {
		return m.statusNote(refusal), nil
	}
	if msg.req.verb == connectorsReconnect {
		return m.startSignIn(c, a.Label(), "Opening "+c.DisplayName+" sign-in for "+a.Label()+"…")
	}
	if a.IsDefault {
		return m.statusNote(a.Label() + " is already the default " + c.DisplayName + " account."), nil
	}
	return m.statusNote("Making " + a.Label() + " the default " + c.DisplayName + " account…"),
		setDefaultAccountCmd(c, a.Label())
}

// pickAccount resolves the account a command names. With none named, a
// connector's only account is the answer; with several, it is the user's
// choice, so the refusal lists them.
func pickAccount(c gaiaconnectors.Connector, account string, verb connectorsVerb) (gaiaconnectors.Account, string) {
	if len(c.Accounts) == 0 {
		return gaiaconnectors.Account{}, c.DisplayName + " has no accounts signed in. Add one: /connectors add " + c.ID
	}
	if account != "" {
		if a, ok := c.FindAccount(account); ok {
			return a, ""
		}
		return gaiaconnectors.Account{}, fmt.Sprintf("%s has no account %q. Its accounts: %s.",
			c.DisplayName, account, c.AccountChoices())
	}
	if len(c.Accounts) == 1 {
		return c.Accounts[0], ""
	}
	return gaiaconnectors.Account{}, fmt.Sprintf("%s has %d accounts: %s. Name one: /connectors %s %s <address>.",
		c.DisplayName, len(c.Accounts), c.AccountChoices(), verb, c.ID)
}

// olderCLINote answers an account command on a CLI that lists no accounts,
// which takes none of the account flags either.
func olderCLINote(c gaiaconnectors.Connector) string {
	return "This GAIA install can't manage " + c.DisplayName + " accounts one at a time. " +
		"Update GAIA, or manage " + c.DisplayName + " under Connectors in the Agent UI."
}

// startSignIn opens a browser sign-in under the context Retire cancels.
func (m ChatModel) startSignIn(c gaiaconnectors.Connector, account, note string) (tea.Model, tea.Cmd) {
	if m.signIns == nil {
		m.signIns, m.stopSignIns = context.WithCancel(context.Background())
	}
	return m.statusNote(note), startSignInCmd(m.signIns, c, account)
}

// removeAccount signs one account of an OAuth connector out of GAIA.
func (m ChatModel) removeAccount(c gaiaconnectors.Connector, account string) (tea.Model, tea.Cmd) {
	// An older CLI lists no accounts and takes no --account; plain disconnect
	// is the same on both.
	if c.Accounts == nil {
		if !c.Configured {
			return m.statusNote(c.DisplayName + " isn't connected."), nil
		}
		if account != "" {
			return m.statusNote(olderCLINote(c)), nil
		}
		return m.statusNote("Disconnecting " + c.DisplayName + "…"), disconnectAccountCmd(c, "")
	}
	a, refusal := pickAccount(c, account, connectorsRemove)
	if refusal != "" {
		return m.statusNote(refusal), nil
	}
	return m.statusNote("Removing " + a.Label() + " from " + c.DisplayName + "…"),
		disconnectAccountCmd(c, a.Label())
}

func (m ChatModel) handleConnectorsSignIn(msg connectorsSignInMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote("Couldn't start the sign-in: " + msg.err.Error()), nil
	}
	return m.statusNote(fmt.Sprintf("Sign in to %s in your browser. If it didn't open, go to:\n%s\n"+
		"GAIA waits up to 15 minutes.", msg.connector.DisplayName, msg.signIn.URL)), waitSignInCmd(msg.signIn)
}

func (m ChatModel) handleConnectorsReport(msg connectorsReportMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote("That didn't work: " + msg.err.Error()), nil
	}
	return m.statusNote(msg.report), nil
}

func (m ChatModel) personalizeConnector(c gaiaconnectors.Connector) (tea.Model, tea.Cmd) {
	if !c.Configured {
		return m.statusNote(fmt.Sprintf("%s isn't connected yet. Connect it from Connectors in the "+
			"Agent UI, then try again.", c.DisplayName)), nil
	}
	if !c.Usable() {
		return m.statusNote(fmt.Sprintf("%s is switched off. Turn it on under Connectors in the "+
			"Agent UI, then try again.", c.DisplayName)), nil
	}
	return m, activateConnectorCmd(c)
}

func (m ChatModel) handleConnectorsActivated(msg connectorsActivatedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote("Couldn't start personalization: " + msg.err.Error()), nil
	}
	// The probe ran in the background; a turn the user started meanwhile keeps
	// the floor, and the pass goes out when it ends.
	if m.streaming {
		return m, tea.Tick(slackOfferRetry, func(time.Time) tea.Msg { return msg })
	}
	if msg.connector.Type == "mcp_server" {
		m = m.statusNote(fmt.Sprintf("GAIA can now use %s, and keeps that access after this pass. "+
			"To turn it off: /connectors revoke %s", msg.connector.DisplayName, msg.connector.ID))
	}
	return m.sendQuery(gaiaconnectors.PersonalizePrompt(msg.connector))
}

// revokeActivation takes back the flagship's standing use of an MCP server.
func (m ChatModel) revokeActivation(c gaiaconnectors.Connector) (tea.Model, tea.Cmd) {
	// An older CLI reports no activations; deactivating is idempotent, so try.
	if c.Activations != nil && !c.FlagshipCanUse() {
		return m.statusNote(fmt.Sprintf("GAIA already can't use %s.", c.DisplayName)), nil
	}
	return m, deactivateConnectorCmd(c)
}

func (m ChatModel) handleConnectorsRevoked(msg connectorsRevokedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote("Couldn't turn off GAIA's access: " + msg.err.Error()), nil
	}
	return m.statusNote(fmt.Sprintf("GAIA can no longer use %s. Turn it back on under Connectors "+
		"in the Agent UI.", msg.connector.DisplayName)), nil
}

// connectorsHint is the status bar's Connectors entry; clicking it runs /connectors.
const connectorsHint = "/connectors"

func (m ChatModel) connectorsHintShown() bool {
	return !m.streaming && m.confirmation == nil && !m.warming && !m.palette.open
}

// clickedConnectorsHint reports whether (x, y) lands on the status bar's
// Connectors entry. The bar is the last row of the screen.
func (m ChatModel) clickedConnectorsHint(x, y int) bool {
	if !m.connectorsHintShown() || y != m.height-1 {
		return false
	}
	plain := ansi.Strip(m.renderStatusBar())
	i := strings.Index(plain, connectorsHint)
	if i < 0 {
		return false
	}
	col := ansi.StringWidth(plain[:i])
	return x >= col && x < col+len(connectorsHint)
}
