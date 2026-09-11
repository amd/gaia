// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiaslack"
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

// slackSetupDoneMsg is delivered when the handed-over setup process exits.
type slackSetupDoneMsg struct{ err error }

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

// runSlackSetupCmd suspends the TUI and runs `gaia slack setup` on the real
// terminal. Setup prompts for two tokens, so it needs the keyboard: run as a
// captured child it would block forever on a prompt nobody can answer — the
// same trap gaiainit avoids by passing --yes.
func runSlackSetupCmd() tea.Cmd {
	bin, args, err := gaiaslack.SetupCommand()
	if err != nil {
		return func() tea.Msg { return slackSetupDoneMsg{err: err} }
	}
	return tea.ExecProcess(exec.Command(bin, args...), func(err error) tea.Msg {
		return slackSetupDoneMsg{err: err}
	})
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

// handleSlackSetupDone renders the outcome of the handed-over setup run.
func (m ChatModel) handleSlackSetupDone(msg slackSetupDoneMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf(
			"Slack setup did not finish: %v\nRun `%s` in a terminal to retry.",
			msg.err, gaiaslack.TypedCommand)), nil
	}
	// Re-probe rather than assume success: the user may have quit setup
	// half-way, and claiming "connected" when no token was stored is worse than
	// saying nothing.
	return m.statusNote("Setup finished — checking…"), querySlackCmd(false)
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
