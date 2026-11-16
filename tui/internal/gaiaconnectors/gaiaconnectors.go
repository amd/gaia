// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiaconnectors asks the `gaia` CLI which connectors exist, which are
// connected and which GAIA may use, turns that use on and off, and builds the
// optional "get to know me" prompt the TUI offers once one is.
//
// Like gaiaslack it shells out: src/gaia/connectors/ owns what "configured"
// means, and `gaia connectors list --json` exists so this can be a thin reader.
package gaiaconnectors

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// ListTimeout bounds the probe; it spawns a Python interpreter. A var so tests
// can shorten it.
var ListTimeout = 30 * time.Second

// AccountTimeout bounds removing an account or changing the default, which
// can wait on the provider to revoke a token.
var AccountTimeout = 2 * time.Minute

// Binary resolves the `gaia` CLI on PATH; a var so tests can stub it.
var Binary = func() (string, error) {
	bin, err := exec.LookPath("gaia")
	if err != nil {
		return "", fmt.Errorf("GAIA is not fully installed on this PC, so " +
			"connectors cannot be listed. Open Connectors in the GAIA desktop " +
			"app, or see https://amd-gaia.ai/docs/guides/install")
	}
	return bin, nil
}

// Connector is one row of `gaia connectors list --json`.
type Connector struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Type        string `json:"type"`
	Category    string `json:"category"`
	Configured  bool   `json:"configured"`
	// Enabled is false for an MCP connector that is configured but switched off.
	// Absent from older CLIs, so it defaults to true.
	Enabled *bool `json:"enabled"`
	// AccountID is the default account's address.
	AccountID string `json:"account_id"`
	// Accounts is every signed-in account of an OAuth connector, oldest first.
	// Absent from older CLIs, which name only AccountID.
	Accounts []Account `json:"accounts"`
	// Activations maps agent id to whether it may use an MCP server's tools.
	// Absent from older CLIs, which then show no access line.
	Activations map[string]bool `json:"activations"`
}

// AccountStatus is how an account's sign-in stands.
type AccountStatus string

const (
	AccountOK AccountStatus = "ok"
	// AccountExpired was refused at refresh; signing in again fixes it.
	AccountExpired AccountStatus = "reauth_required"
	// AccountBroken is a sign-in that did not finish saving.
	AccountBroken AccountStatus = "error"
)

// Account is one signed-in account of an OAuth connector.
type Account struct {
	ID        string        `json:"account_id"`
	Email     string        `json:"email"`
	Status    AccountStatus `json:"status"`
	IsDefault bool          `json:"is_default"`
}

// Label is how the user names the account: its address, else its id.
func (a Account) Label() string {
	if a.Email != "" {
		return a.Email
	}
	return a.ID
}

// FindAccount returns the account whose address or id matches, ignoring case.
func (c Connector) FindAccount(name string) (Account, bool) {
	for _, a := range c.Accounts {
		if strings.EqualFold(a.Email, name) || strings.EqualFold(a.ID, name) {
			return a, true
		}
	}
	return Account{}, false
}

// AccountChoices lists the accounts' labels, for naming the choices.
func (c Connector) AccountChoices() string {
	labels := make([]string, len(c.Accounts))
	for i, a := range c.Accounts {
		labels[i] = a.Label()
	}
	return strings.Join(labels, ", ")
}

// listsAccounts reports whether /connectors shows a line per account: there
// are several, or one whose sign-in is not working.
func (c Connector) listsAccounts() bool {
	if len(c.Accounts) > 1 {
		return true
	}
	return len(c.Accounts) == 1 && c.Accounts[0].Status != AccountOK
}

// List runs `gaia connectors list --json`.
func List(ctx context.Context) ([]Connector, error) {
	bin, err := Binary()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, ListTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "connectors", "list", "--json")
	var out, errBuf bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errBuf
	if runErr := cmd.Run(); runErr != nil {
		return nil, fmt.Errorf("`gaia connectors list --json` failed (%w): %s",
			runErr, strings.TrimSpace(errBuf.String()))
	}
	return Parse(out.Bytes())
}

// Parse decodes the CLI's JSON, skipping any log lines printed before it.
func Parse(raw []byte) ([]Connector, error) {
	if i := bytes.IndexByte(raw, '['); i > 0 {
		raw = raw[i:]
	}
	var rows []Connector
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, fmt.Errorf("`gaia connectors list --json` returned something "+
			"that is not a connector list: %w", err)
	}
	return rows, nil
}

// Usable reports whether the connector is connected and switched on.
func (c Connector) Usable() bool {
	return c.Configured && (c.Enabled == nil || *c.Enabled)
}

// FlagshipAgentID is the agent a personalization chat runs on.
const FlagshipAgentID = "installed:gaia"

// FlagshipCanUse reports whether the flagship may use an MCP connector's
// tools. Personalize turns this on and it stays on until revoked.
func (c Connector) FlagshipCanUse() bool {
	return c.Type == "mcp_server" && c.Activations[FlagshipAgentID]
}

// Activate lets the flagship see an MCP connector's tools. They stay hidden
// until it is activated for the agent, so a pass run without this has nothing
// to read. OAuth connectors need nothing here.
func Activate(ctx context.Context, c Connector) error {
	if c.Type != "mcp_server" {
		return nil
	}
	return runActivations(ctx, "let GAIA use", c, "activate", "--scopes", "use")
}

// Deactivate takes back the flagship's use of an MCP connector's tools. The
// grant stays, as with the Agent UI's per-agent toggle.
func Deactivate(ctx context.Context, c Connector) error {
	if c.Type != "mcp_server" {
		return fmt.Errorf("GAIA's access to %s comes with connecting it. Disconnect it "+
			"under Connectors in the Agent UI to remove that", c.DisplayName)
	}
	return runActivations(ctx, "turn off GAIA's access to", c, "deactivate")
}

func runActivations(ctx context.Context, what string, c Connector, sub string, extra ...string) error {
	args := append([]string{"connectors", "activations", sub, c.ID, FlagshipAgentID}, extra...)
	_, err := runCLI(ctx, ListTimeout, what+" "+c.DisplayName, args...)
	return err
}

// runCLI runs `gaia <args>` and returns what it printed. what completes
// "could not …" in the error.
func runCLI(ctx context.Context, timeout time.Duration, what string, args ...string) (string, error) {
	bin, err := Binary()
	if err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, args...)
	var out, errBuf bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errBuf
	if runErr := cmd.Run(); runErr != nil {
		return "", fmt.Errorf("could not %s (`gaia %s` failed: %w): %s", what,
			strings.Join(args, " "), runErr, strings.TrimSpace(errBuf.String()))
	}
	return strings.TrimSpace(out.String()), nil
}

// Disconnect signs one account of an OAuth connector out of GAIA. An empty
// account disconnects the connector, which the CLI refuses when it has
// several accounts. It returns the CLI's report.
func Disconnect(ctx context.Context, c Connector, account string) (string, error) {
	args := []string{"connectors", "disconnect", c.ID}
	what := "disconnect " + c.DisplayName
	if account != "" {
		args = append(args, "--account", account)
		what = "remove " + account + " from " + c.DisplayName
	}
	return runCLI(ctx, AccountTimeout, what, args...)
}

// SetDefault makes account the one a connector uses when none is named.
func SetDefault(ctx context.Context, c Connector, account string) (string, error) {
	return runCLI(ctx, AccountTimeout, "make "+account+" the default "+c.DisplayName+" account",
		"connectors", "set-default", c.ID, account)
}

// SignInTimeout outlasts the CLI's own 15-minute wait for the browser.
const SignInTimeout = 16 * time.Minute

// signInWaitDelay is how long a finished sign-in waits for its output to
// close. On Linux the browser the CLI opens inherits that output and keeps it
// open long after the CLI exits, so the CLI's exit is what ends the sign-in.
const signInWaitDelay = 2 * time.Second

// SignIn is a browser sign-in that `gaia connectors connect` is waiting on.
type SignIn struct {
	// URL is the sign-in page. The CLI opens it in the browser too.
	URL    string
	out    *signInOutput
	stderr *bytes.Buffer
	cancel context.CancelFunc
	// done closes once the CLI has exited; err is how it exited.
	done chan struct{}
	err  error
	// what completes "could not …"; command is what ran, for the error.
	what, command string
}

// StartSignIn signs another account in (account == "") or signs a listed
// account in again, and returns once the CLI has printed the sign-in link.
// Cancelling ctx stops the CLI.
func StartSignIn(ctx context.Context, c Connector, account string) (*SignIn, error) {
	bin, err := Binary()
	if err != nil {
		return nil, err
	}
	args := []string{"connectors", "connect", c.ID, "--add"}
	what := "add a " + c.DisplayName + " account"
	if account != "" {
		args = []string{"connectors", "connect", c.ID, "--account", account}
		what = "reconnect " + account
	}
	ctx, cancel := context.WithTimeout(ctx, SignInTimeout)
	cmd := exec.CommandContext(ctx, bin, args...)
	cmd.WaitDelay = signInWaitDelay
	s := &SignIn{out: &signInOutput{link: make(chan string, 1)}, stderr: &bytes.Buffer{},
		cancel: cancel, done: make(chan struct{}), what: what, command: "gaia " + strings.Join(args, " ")}
	cmd.Stdout = s.out
	cmd.Stderr = s.stderr
	if err := cmd.Start(); err != nil {
		cancel()
		return nil, fmt.Errorf("could not %s (`%s` failed: %w)", s.what, s.command, err)
	}
	go func() {
		err := cmd.Wait()
		if errors.Is(err, exec.ErrWaitDelay) {
			err = nil
		}
		s.out.flush()
		s.err = err
		close(s.done)
	}()
	select {
	case s.URL = <-s.out.link:
		return s, nil
	case <-s.done:
	}
	select {
	case s.URL = <-s.out.link:
		return s, nil
	default:
	}
	_, err = s.Wait()
	if err == nil {
		err = fmt.Errorf("could not %s: `%s` printed no sign-in link", s.what, s.command)
	}
	return nil, err
}

// Wait blocks until the CLI exits and returns its closing line, such as
// "Added jane@example.com".
func (s *SignIn) Wait() (string, error) {
	<-s.done
	s.cancel()
	if s.err != nil {
		return "", fmt.Errorf("could not %s (`%s` failed: %w): %s", s.what, s.command, s.err,
			strings.TrimSpace(s.stderr.String()))
	}
	return s.out.lastLine(), nil
}

// signInOutput reads the CLI's output line by line: it hands the first link
// to StartSignIn and keeps the last line for Wait.
type signInOutput struct {
	mu      sync.Mutex
	partial []byte
	last    string
	link    chan string
	linked  bool
}

func (o *signInOutput) Write(p []byte) (int, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.partial = append(o.partial, p...)
	for {
		i := bytes.IndexByte(o.partial, '\n')
		if i < 0 {
			return len(p), nil
		}
		o.line(string(o.partial[:i]))
		o.partial = o.partial[i+1:]
	}
}

// flush reads a last line that ended without a newline.
func (o *signInOutput) flush() {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.line(string(o.partial))
	o.partial = nil
}

func (o *signInOutput) line(text string) {
	text = strings.TrimSpace(text)
	if text == "" {
		return
	}
	o.last = text
	if !o.linked && (strings.HasPrefix(text, "https://") || strings.HasPrefix(text, "http://")) {
		o.linked = true
		o.link <- text
	}
}

func (o *signInOutput) lastLine() string {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.last
}

// Find returns the connector whose id or display name matches, ignoring case.
func Find(rows []Connector, name string) (Connector, bool) {
	for _, c := range rows {
		if strings.EqualFold(c.ID, name) || strings.EqualFold(c.DisplayName, name) {
			return c, true
		}
	}
	return Connector{}, false
}

// Render is what /connectors prints.
func Render(rows []Connector) string {
	if len(rows) == 0 {
		return "No connectors are available in this GAIA install."
	}
	var b strings.Builder
	b.WriteString("Connectors\n\n")
	anyConnected := false
	var withAccess, withAccounts, withSeveral []string
	for _, c := range rows {
		status := "not connected"
		switch {
		case c.Usable():
			anyConnected = true
			status = "connected"
			if len(c.Accounts) > 1 {
				status += fmt.Sprintf(", %d accounts", len(c.Accounts))
			} else if c.AccountID != "" && !c.listsAccounts() {
				status += " as " + c.AccountID
			}
		case c.Configured:
			status = "connected but switched off"
		case len(c.Accounts) > 0:
			status = "needs signing in again"
		}
		if c.Usable() && c.FlagshipCanUse() {
			status += " · GAIA can use it"
			withAccess = append(withAccess, c.ID)
		}
		fmt.Fprintf(&b, "  %-24s %s\n", c.DisplayName+" ("+c.ID+")", status)
		if c.Type == "oauth_pkce" && len(c.Accounts) > 0 {
			withAccounts = append(withAccounts, c.ID)
		}
		if len(c.Accounts) > 1 {
			withSeveral = append(withSeveral, c.ID)
		}
		if c.listsAccounts() {
			for _, a := range c.Accounts {
				b.WriteString("      " + a.Label())
				if note := accountNote(c, a); note != "" {
					b.WriteString(" (" + note + ")")
				}
				b.WriteString("\n")
			}
		}
	}
	b.WriteString("\nConnect one from Connectors in the Agent UI.")
	if len(withAccounts) > 0 {
		b.WriteString("\nAnother account: /connectors add <id>. Sign one out of GAIA: /connectors remove <id> <address>.")
	}
	if len(withSeveral) > 0 {
		b.WriteString("\nChange which account GAIA uses when you don't say: /connectors default <id> <address>.")
	}
	if anyConnected {
		b.WriteString("\nOptional: /connectors personalize <id> lets GAIA read recent activity, " +
			"save what it learns to memory, and suggest automations. GAIA keeps access " +
			"to that app afterwards.")
	}
	if len(withAccess) > 0 {
		b.WriteString("\nTo turn off GAIA's access: /connectors revoke <id> (" +
			strings.Join(withAccess, ", ") + ").")
	}
	return b.String()
}

// accountNote is the text beside an account on its own line.
func accountNote(c Connector, a Account) string {
	var notes []string
	if a.IsDefault {
		notes = append(notes, "default")
	}
	switch a.Status {
	case AccountExpired:
		notes = append(notes, "sign-in expired: /connectors reconnect "+c.ID+" "+a.Label())
	case AccountBroken:
		notes = append(notes, "sign-in did not finish: /connectors remove "+c.ID+" "+a.Label()+
			", then /connectors add "+c.ID)
	}
	return strings.Join(notes, " · ")
}

var sources = []struct{ prefix, what string }{
	{"google", "my last two weeks of Gmail (and Google Calendar, if it is connected)"},
	{"microsoft", "my last two weeks of Outlook mail (and calendar, if it is connected)"},
	{"slack", "my recent Slack channels and direct messages"},
	{"github", "my recent GitHub activity: open PRs, review requests, issues and notifications"},
}

// Source names what the personalization pass reads for a connector. It mirrors
// src/gaia/apps/webui/src/utils/connectorPersonalize.ts.
func Source(c Connector) string {
	id := strings.TrimPrefix(strings.ToLower(c.ID), "mcp-")
	for _, s := range sources {
		if strings.HasPrefix(id, s.prefix) {
			return s.what
		}
	}
	return "my recent activity in " + c.DisplayName
}

// PersonalizePrompt is the chat prompt for the optional personalization pass.
func PersonalizePrompt(c Connector) string {
	return strings.Join([]string{
		"I just connected " + c.DisplayName + ". Get to know me from " + Source(c) + ".",
		"Read only — do not send, delete, label or change anything.",
		"If you have no tools that read it, or they fail, tell me you couldn't reach it and stop. Never guess or count what it holds.",
		"1. Skim the recent items and work out who I deal with most, what I work on, recurring meetings or threads, and what keeps landing on my plate.",
		"2. Save the durable facts to memory (people, projects, preferences, routines). Skip one-off details and anything sensitive like passwords, codes or financial numbers.",
		"3. Recommend up to five automations you could run for me on a schedule, each with what it does, how often, and why it fits what you saw.",
		"Don't create any schedules yet — tell me what you saved and list the automations so I can pick which to set up.",
	}, "\n")
}
