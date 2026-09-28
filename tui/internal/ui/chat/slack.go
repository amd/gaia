// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiaslack"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// `/slack` and the one-time offer that precedes it.
//
// The offer is the whole point: a user who has Slack installed should be told
// GAIA can use it, exactly once, without having to go looking. Everything about
// WHEN that is true lives in `gaia slack status --json` — this file renders the
// answer and records the reply, and deliberately re-derives none of the rule.
//
// Setup itself is interactive: it opens a browser and reads two pasted tokens.
// A captured child cannot do that, so the offer hands the terminal over with
// tea.ExecProcess and takes it back when setup exits.

// slackStatusMsg carries the result of the read-only probe.
type slackStatusMsg struct {
	status gaiaslack.Status
	// err is set only when the question could not be ASKED. A machine with no
	// Slack is a clean answer, not an error.
	err error
	// offer marks a probe run at launch, whose answer may open the offer, as
	// opposed to one the user asked for with /slack.
	offer bool
}

// slackDeclinedMsg is delivered once a skip/never has been recorded.
type slackDeclinedMsg struct {
	never bool
	err   error
}

// querySlackCmd asks the CLI about Slack without changing anything.
func querySlackCmd(offer bool) tea.Cmd {
	return func() tea.Msg {
		status, err := gaiaslack.Query(context.Background())
		return slackStatusMsg{status: status, err: err, offer: offer}
	}
}

// declineSlackCmd records "not now" or "stop asking".
func declineSlackCmd(never bool) tea.Cmd {
	return func() tea.Msg {
		err := gaiaslack.Decline(context.Background(), never)
		return slackDeclinedMsg{never: never, err: err}
	}
}

// Setup runs INSIDE the TUI — it never hands the terminal over.
//
// The obvious implementation, tea.ExecProcess around `gaia slack setup`, was
// the first one here and it is wrong for three reasons: a suspended TUI cannot
// be drawn, cannot be driven by the control API (which exists so an assistant
// can navigate the TUI while the user watches), and cannot be exercised by the
// end-to-end harness. It also reads as the app crashing and coming back.
//
// So the panel collects both tokens with the same QuestionModel the agent's own
// mid-run questions use — sensitive:true, so a pasted token is masked — and
// hands them to `gaia slack connect` on STDIN, never argv.

// slackSetupStep is where the panel is in the three-question flow.
type slackSetupStep int

const (
	slackStepIdle slackSetupStep = iota
	slackStepConfirmCreated
	slackStepAppToken
	slackStepBotToken
	slackStepConnecting
)

// slackQuestionPrefix marks a QuestionModel this file owns, so an answer is
// handled locally instead of being posted to the agent as a mid-run reply.
const slackQuestionPrefix = "gaia-slack-setup:"

// slackSetupState is the in-flight setup, if any.
type slackSetupState struct {
	step     slackSetupStep
	url      string
	appToken string
}

// slackURLMsg carries the pre-filled create-app URL once the CLI has built it.
type slackURLMsg struct {
	url string
	err error
}

// slackConnectedMsg is the result of storing the tokens.
type slackConnectedMsg struct {
	team string
	err  error
}

// fetchSlackURLCmd asks the CLI for the create-app URL. Asked rather than
// rebuilt in Go: the manifest is a security surface and two copies would drift.
func fetchSlackURLCmd() tea.Cmd {
	return func() tea.Msg {
		url, err := gaiaslack.CreateAppURL(context.Background())
		return slackURLMsg{url: url, err: err}
	}
}

// connectSlackCmd validates and stores the pair.
func connectSlackCmd(appToken, botToken string) tea.Cmd {
	return func() tea.Msg {
		team, err := gaiaslack.Connect(context.Background(), appToken, botToken)
		return slackConnectedMsg{team: team, err: err}
	}
}

// startSlackSetup opens the panel.
func (m ChatModel) startSlackSetup() (tea.Model, tea.Cmd) {
	m.slackSetup = &slackSetupState{step: slackStepConfirmCreated}
	return m.statusNote("Preparing Slack setup…"), fetchSlackURLCmd()
}

// handleSlackURL shows the URL and asks the first question.
func (m ChatModel) handleSlackURL(msg slackURLMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		m.slackSetup = nil
		return m.statusNote(fmt.Sprintf(
			"Slack setup could not start: %v", msg.err)), nil
	}
	if m.slackSetup == nil {
		return m, nil
	}
	m.slackSetup.url = msg.url

	m = m.statusNote(strings.Join([]string{
		"Slack apps can only be created at api.slack.com, so this part is not",
		"automatic. The manifest — scopes, Socket Mode, the DM subscription —",
		"is filled in for you.",
		"",
		"1. Open this and click Create (your browser may already be there):",
		"   " + msg.url,
		"2. Basic Information → App-Level Tokens → Generate, add the",
		"   'connections:write' scope, copy the 'xapp-' token.",
		"3. OAuth & Permissions → Install to Workspace, copy the 'xoxb-' token.",
	}, "\n"))

	q := components.NewQuestionModel(
		slackQuestionPrefix+"created",
		"Created the app and got both tokens?",
		[]components.QuestionOption{
			{Value: "yes", Label: "Yes, I have both tokens",
				Description: "Paste them next — they are masked and go straight to your OS keyring"},
			{Value: "cancel", Label: "Cancel",
				Description: "Nothing is stored; /slack setup starts over"},
		},
		false, /* allowFreeText */
		false, /* sensitive */
	)
	m.question = &q
	m.updateViewport()
	return m, nil
}

// askSlackToken puts up one masked free-text prompt.
func (m ChatModel) askSlackToken(id, prompt string) ChatModel {
	q := components.NewQuestionModel(
		slackQuestionPrefix+id,
		prompt,
		nil,  /* options — free text only */
		true, /* allowFreeText */
		true, /* sensitive: a token must not be echoed into the scrollback */
	)
	m.question = &q
	m.updateViewport()
	return m
}

// isSlackSetupQuestion reports whether an answer belongs to this flow rather
// than to a question the agent asked mid-run.
func isSlackSetupQuestion(requestID string) bool {
	return strings.HasPrefix(requestID, slackQuestionPrefix)
}

// handleSlackSetupAnswer advances the flow. The caller has already cleared
// m.question.
func (m ChatModel) handleSlackSetupAnswer(requestID, value string) (tea.Model, tea.Cmd) {
	if m.slackSetup == nil {
		return m, nil
	}
	switch strings.TrimPrefix(requestID, slackQuestionPrefix) {
	case "created":
		if value != "yes" {
			m.slackSetup = nil
			return m.statusNote(
				"Cancelled — nothing was stored. Type /slack setup to start over."), nil
		}
		m.slackSetup.step = slackStepAppToken
		return m.askSlackToken("app-token",
			"Paste the app-level token (starts with 'xapp-')"), nil

	case "app-token":
		token := strings.TrimSpace(value)
		if token == "" {
			return m.askSlackToken("app-token",
				"That was empty. Paste the app-level token (starts with 'xapp-')"), nil
		}
		m.slackSetup.appToken = token
		m.slackSetup.step = slackStepBotToken
		return m.askSlackToken("bot-token",
			"Paste the bot user OAuth token (starts with 'xoxb-')"), nil

	case "bot-token":
		token := strings.TrimSpace(value)
		if token == "" {
			return m.askSlackToken("bot-token",
				"That was empty. Paste the bot token (starts with 'xoxb-')"), nil
		}
		appToken := m.slackSetup.appToken
		m.slackSetup.step = slackStepConnecting
		// Dropped from the model the moment it is handed off: the flow has no
		// further use for it, and a token sitting in UI state outlives the
		// screen it was typed on.
		m.slackSetup.appToken = ""
		return m.statusNote("Checking the tokens with Slack…"),
			connectSlackCmd(appToken, token)
	}
	return m, nil
}

// handleSlackConnected reports the outcome.
func (m ChatModel) handleSlackConnected(msg slackConnectedMsg) (tea.Model, tea.Cmd) {
	m.slackSetup = nil
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf(
			"Slack setup failed: %v\n\nType /slack setup to try again.",
			msg.err)), nil
	}
	return m.statusNote(strings.Join([]string{
		"Connected to " + msg.team + ". Tokens are in your OS keyring.",
		"",
		"Before starting it, decide who may message it — everyone in a",
		"workspace can DM a bot, and this bridge can read your files and ask",
		"to run commands:",
		"",
		"  gaia slack start --allowed-users <your member ID>",
		"",
		"Your member ID: Slack → your avatar → Profile → ... → Copy member ID.",
	}, "\n")), nil
}

// startSlackCheck runs the probe for the /slack command.
func (m ChatModel) startSlackCheck() (tea.Model, tea.Cmd) {
	return m.statusNote("Checking Slack…"), querySlackCmd(false)
}

// handleSlackStatus renders a probe result.
func (m ChatModel) handleSlackStatus(msg slackStatusMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		if msg.offer {
			// At launch this is background work the user never asked for.
			// Interrupting them with its failure would be noise; /slack still
			// reports it on demand.
			return m, nil
		}
		return m.statusNote(slackUnavailableNote(msg.err)), nil
	}
	if msg.offer {
		// Two guards, and they answer different questions. ShouldOfferSetup is
		// the DURABLE one -- has this user already said no? slackOffered is the
		// session one: a probe that somehow ran twice must not print the offer
		// twice into the same transcript.
		if !msg.status.ShouldOfferSetup || m.slackOffered {
			return m, nil
		}
		m.slackOffered = true
		return m.statusNote(slackOfferText()), nil
	}
	return m.statusNote(slackStatusText(msg.status)), nil
}

// handleSlackDeclined renders the recorded decision.
func (m ChatModel) handleSlackDeclined(msg slackDeclinedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf(
			"Could not record that: %v\nRun `gaia slack decline` yourself, or "+
				"ignore the offer — it is not shown twice in one session.",
			msg.err)), nil
	}
	if msg.never {
		return m.statusNote(
			"Won't ask about Slack again. Type /slack whenever you change your mind.",
		), nil
	}
	return m.statusNote(
		"Skipped. Type /slack whenever you want to connect it.",
	), nil
}

// slackOfferText is the one-time offer.
func slackOfferText() string {
	return strings.Join([]string{
		"Slack is installed on this machine — GAIA can answer there too.",
		"",
		"You'd message GAIA from Slack on any device and it would answer from",
		"here, with access to your files. Setup takes about ninety seconds.",
		"",
		"  /slack setup   connect it now",
		"  /slack skip    not now (offered again only if something changes)",
		"  /slack never   stop asking",
	}, "\n")
}

// slackStatusText is what /slack reports.
func slackStatusText(s gaiaslack.Status) string {
	lines := []string{"Slack: " + s.Summary()}
	switch {
	case s.Running:
		lines = append(lines, "", "Send it a direct message.")
	case s.Configured:
		lines = append(lines, "",
			"Start it with:",
			"  gaia slack start --allowed-users <your member ID>")
	default:
		lines = append(lines, "",
			"Connect it with:",
			"  "+gaiaslack.TypedCommand,
			"",
			"Or type /slack setup to do it from here.")
	}
	return strings.Join(lines, "\n")
}

// slackUnavailableNote explains a probe that could not run.
func slackUnavailableNote(err error) string {
	if errors.Is(err, gaiaslack.ErrUnanswered) {
		return fmt.Sprintf(
			"Could not check Slack: %v\n\nThis says nothing about whether Slack "+
				"is set up — only that the question could not be asked.", err)
	}
	return fmt.Sprintf("Could not check Slack: %v", err)
}
