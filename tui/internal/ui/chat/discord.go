// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/gaiadiscord"
	"github.com/amd/gaia/tui/internal/ui/components"
)

// `/discord`: message GAIA from a Discord DM.
//
// Setup runs inside the TUI and opens every page the user needs as they reach
// it: the Developer Portal for the token, the invite page to add the bot to a
// server, and the bot's profile to DM it the pairing code. The token is typed
// into a masked prompt and handed to `gaia discord connect` on stdin. Nothing
// here tells the user to run a command — every step is a slash command or a
// page that opens on its own.

// discordPollEvery is how often an open pairing step checks whether the code
// has been sent.
var discordPollEvery = 3 * time.Second

// discordPollFor bounds that check; a pairing code lives an hour.
const discordPollFor = 60 * time.Minute

const discordQuestionPrefix = "gaia-discord-setup:"

// discordSetupState is the in-flight setup, if any.
type discordSetupState struct {
	// pollUntil is when the pairing check gives up; zero while not pairing.
	pollUntil time.Time
	// shownCode is the code last printed, so a rotated one is announced.
	shownCode string
}

type discordStatusMsg struct {
	status gaiadiscord.Status
	err    error
	// offer marks the probe run at launch, whose answer may open the offer.
	offer bool
}

type discordConnectedMsg struct {
	result gaiadiscord.ConnectResult
	status gaiadiscord.Status
	err    error
}

type discordActionMsg struct {
	what   string
	status gaiadiscord.Status
	err    error
}

type discordPollMsg struct {
	status gaiadiscord.Status
	err    error
}

type discordDeclinedMsg struct {
	never bool
	err   error
}

func queryDiscordCmd(offer bool) tea.Cmd {
	return func() tea.Msg {
		status, err := gaiadiscord.Query(context.Background())
		return discordStatusMsg{status: status, err: err, offer: offer}
	}
}

// launchDiscordCmd brings back a bridge that is set to autostart, then asks
// whether to offer setup. A failed autostart is reported by the status it
// leaves behind, which /discord shows.
func launchDiscordCmd() tea.Cmd {
	return func() tea.Msg {
		_ = gaiadiscord.Autostart(context.Background())
		status, err := gaiadiscord.Query(context.Background())
		return discordStatusMsg{status: status, err: err, offer: true}
	}
}

func pollDiscordCmd() tea.Cmd {
	return tea.Tick(discordPollEvery, func(time.Time) tea.Msg {
		status, err := gaiadiscord.Query(context.Background())
		return discordPollMsg{status: status, err: err}
	})
}

func connectDiscordCmd(token, model string) tea.Cmd {
	return func() tea.Msg {
		ctx := context.Background()
		result, err := gaiadiscord.Connect(ctx, token)
		if err != nil {
			return discordConnectedMsg{err: err}
		}
		status, err := gaiadiscord.Start(ctx, model)
		return discordConnectedMsg{result: result, status: status, err: err}
	}
}

func discordActionCmd(what string, fn func(context.Context) (gaiadiscord.Status, error)) tea.Cmd {
	return func() tea.Msg {
		status, err := fn(context.Background())
		return discordActionMsg{what: what, status: status, err: err}
	}
}

func declineDiscordCmd(never bool) tea.Cmd {
	return func() tea.Msg {
		return discordDeclinedMsg{never: never, err: gaiadiscord.Decline(context.Background(), never)}
	}
}

func isDiscordSetupQuestion(requestID string) bool {
	return strings.HasPrefix(requestID, discordQuestionPrefix)
}

// handleDiscordCommand routes every `/discord …` line.
func (m ChatModel) handleDiscordCommand(query string) (tea.Model, tea.Cmd) {
	ctx := func(fn func(context.Context) error) func(context.Context) (gaiadiscord.Status, error) {
		return func(c context.Context) (gaiadiscord.Status, error) {
			if err := fn(c); err != nil {
				return gaiadiscord.Status{}, err
			}
			return gaiadiscord.Query(c)
		}
	}
	model := m.modelID
	switch strings.TrimSpace(strings.TrimPrefix(query, "/discord")) {
	case "":
		return m.statusNote("Checking Discord…"), queryDiscordCmd(false)
	case "setup":
		return m.startDiscordSetup()
	case "start":
		return m.statusNote("Starting the Discord bridge…"),
			discordActionCmd("start", func(c context.Context) (gaiadiscord.Status, error) {
				return gaiadiscord.Start(c, model)
			})
	case "restart":
		return m.statusNote("Restarting the Discord bridge…"),
			discordActionCmd("restart", func(c context.Context) (gaiadiscord.Status, error) {
				return gaiadiscord.Restart(c, model, false)
			})
	case "takeover":
		return m.statusNote("Answering Discord from this PC…"),
			discordActionCmd("takeover", func(c context.Context) (gaiadiscord.Status, error) {
				return gaiadiscord.Restart(c, model, true)
			})
	case "pair":
		return m.statusNote("Starting a new pairing…"), discordActionCmd("pair", gaiadiscord.Pair)
	case "stop":
		return m.statusNote("Stopping the Discord bridge…"), discordActionCmd("stop", ctx(gaiadiscord.Stop))
	case "disconnect":
		return m.statusNote("Disconnecting Discord…"), discordActionCmd("disconnect", ctx(gaiadiscord.Disconnect))
	case "skip":
		return m, declineDiscordCmd(false)
	case "never":
		return m, declineDiscordCmd(true)
	}
	return m.statusNote(discordHelpText()), nil
}

// startDiscordSetup opens the portal and asks for the token.
func (m ChatModel) startDiscordSetup() (tea.Model, tea.Cmd) {
	if m.discordSetup != nil && m.question != nil && isDiscordSetupQuestion(m.question.RequestID()) {
		return m.statusNote("Discord setup is already open — paste the token, or press Esc."), nil
	}
	m.discordSetup = &discordSetupState{}
	m = m.statusNote(strings.Join([]string{
		"Connect GAIA to Discord — about two minutes. Your browser is opening the",
		"Discord Developer Portal (" + gaiadiscord.PortalURL + ").",
		"",
		"1. Click New Application, name it (GAIA works), and click Create.",
		"2. In the left menu open Bot, click Reset Token, then Copy.",
		"3. Paste it below. It is masked and goes straight to your OS keyring.",
		"",
		"No intents or permissions need turning on. Only you can use it: GAIA",
		"answers DMs from the one account you pair, and anything that changes",
		"your PC waits for you to press Allow.",
	}, "\n"))
	q := components.NewQuestionModel(
		discordQuestionPrefix+"token",
		"Paste the bot token",
		nil,
		true, /* allowFreeText */
		true, /* sensitive: never echoed into the scrollback */
	)
	m.question = &q
	m.updateViewport()
	return m, openURL(gaiadiscord.PortalURL)
}

// handleDiscordSetupAnswer takes the token. The caller has cleared m.question.
func (m ChatModel) handleDiscordSetupAnswer(requestID, value string) (tea.Model, tea.Cmd) {
	if m.discordSetup == nil {
		return m, nil
	}
	token := strings.TrimSpace(value)
	if token == "" {
		q := components.NewQuestionModel(discordQuestionPrefix+"token",
			"That was empty. Paste the bot token (Bot → Reset Token → Copy)", nil, true, true)
		m.question = &q
		m.updateViewport()
		return m, nil
	}
	return m.statusNote("Checking the token with Discord and starting the bridge…"),
		connectDiscordCmd(token, m.modelID)
}

// handleDiscordConnected shows the invite and the pairing code.
func (m ChatModel) handleDiscordConnected(msg discordConnectedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		m.discordSetup = nil
		return m.statusNote(fmt.Sprintf(
			"Discord setup failed: %v\n\nType /discord setup to try again.", msg.err)), nil
	}
	lines := []string{"Token accepted for " + msg.result.BotName + ". It is in your OS keyring."}
	if msg.result.BotPublic {
		lines = append(lines, "",
			"Tip: Public Bot is on, so anyone could add this bot to their server and",
			"DM it. They would only be refused, but you can switch it off under",
			"Bot → Public Bot: "+msg.result.BotPageURL)
	}
	m = m.statusNote(strings.Join(lines, "\n"))
	if m.discordSetup == nil {
		m.discordSetup = &discordSetupState{}
	}
	next, cmd := m.showDiscordPairing(msg.status, true)
	return next, tea.Batch(cmd, openURL(msg.result.InviteURL))
}

// showDiscordPairing prints the pairing steps and starts watching for them.
func (m ChatModel) showDiscordPairing(s gaiadiscord.Status, openedInvite bool) (ChatModel, tea.Cmd) {
	if s.State != gaiadiscord.StatePairing || s.PairingCode == "" {
		return m.statusNote(discordStatusText(s)), nil
	}
	if m.discordSetup == nil {
		m.discordSetup = &discordSetupState{}
	}
	m.discordSetup.shownCode = s.PairingCode
	m.discordSetup.pollUntil = time.Now().Add(discordPollFor)
	opening := "Open the invite page and pick that server:"
	if openedInvite {
		opening = "Your browser is opening the invite page — pick that server:"
	}
	m = m.statusNote(strings.Join([]string{
		"4. Add the bot to a private server. Discord only lets you DM a bot you share",
		"   a server with, so make one only you are in: in Discord click + (Add a Server)",
		"   → Create My Own → For me and my friends → Create. " + opening,
		"   " + s.InviteURL,
		"   The bot gets no permissions there, and GAIA ignores everything in servers.",
		"",
		"5. DM the bot this code. Open its profile and click Message:",
		"   " + s.BotProfileURL,
		"",
		"      " + formatPairingCode(s.PairingCode),
		"",
		"Whoever sends the code is the only person GAIA will answer. This screen",
		"says so as soon as it arrives.",
	}, "\n"))
	return m, pollDiscordCmd()
}

func (m ChatModel) handleDiscordPoll(msg discordPollMsg) (tea.Model, tea.Cmd) {
	setup := m.discordSetup
	if setup == nil || setup.pollUntil.IsZero() {
		return m, nil
	}
	if msg.err == nil && msg.status.Paired && msg.status.State == gaiadiscord.StateConnected {
		m.discordSetup = nil
		return m.statusNote(discordPairedText(msg.status)), nil
	}
	if time.Now().After(setup.pollUntil) {
		m.discordSetup = nil
		return m.statusNote("Still not paired. Type /discord to see the current code."), nil
	}
	if msg.err == nil && msg.status.State == gaiadiscord.StatePairing &&
		msg.status.PairingCode != "" && msg.status.PairingCode != setup.shownCode {
		setup.shownCode = msg.status.PairingCode
		m = m.statusNote("The pairing code changed (it expired or had too many wrong guesses). " +
			"Send this one instead:  " + formatPairingCode(msg.status.PairingCode))
	}
	if msg.err == nil && msg.status.State != gaiadiscord.StatePairing &&
		msg.status.State != gaiadiscord.StateStarting {
		m.discordSetup = nil
		return m.statusNote(discordStatusText(msg.status)), nil
	}
	return m, pollDiscordCmd()
}

func (m ChatModel) handleDiscordStatus(msg discordStatusMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		if msg.offer {
			return m, nil
		}
		return m.statusNote(discordUnavailableNote(msg.err)), nil
	}
	if msg.offer {
		if !msg.status.ShouldOfferSetup || m.discordOffered {
			return m, nil
		}
		if m.streaming {
			return m, tea.Tick(slackOfferRetry, func(time.Time) tea.Msg { return msg })
		}
		m.discordOffered = true
		return m.statusNote(discordOfferText()), nil
	}
	if msg.status.State == gaiadiscord.StatePairing {
		return m.showDiscordPairing(msg.status, false)
	}
	return m.statusNote(discordStatusText(msg.status)), nil
}

func (m ChatModel) handleDiscordAction(msg discordActionMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf("Discord %s failed: %v", msg.what, msg.err)), nil
	}
	if msg.status.State == gaiadiscord.StatePairing {
		return m.showDiscordPairing(msg.status, false)
	}
	return m.statusNote(discordStatusText(msg.status)), nil
}

func (m ChatModel) handleDiscordDeclined(msg discordDeclinedMsg) (tea.Model, tea.Cmd) {
	if msg.err != nil {
		return m.statusNote(fmt.Sprintf("Could not record that: %v", msg.err)), nil
	}
	if msg.never {
		return m.statusNote("Won't ask about Discord again. Type /discord setup whenever you want it."), nil
	}
	return m.statusNote("Skipped. Type /discord setup whenever you want to connect it."), nil
}

func formatPairingCode(code string) string {
	if len(code) == 8 {
		return code[:4] + "-" + code[4:]
	}
	return code
}

func discordPairedText(s gaiadiscord.Status) string {
	model := ""
	if s.Model != "" {
		model = " with " + s.Model
	}
	return strings.Join([]string{
		"Paired with " + s.OwnerName + ". DM " + s.BotName + " anything — GAIA answers from this PC" + model + ".",
		"Shell commands and file changes wait for you to press Allow in Discord.",
		"",
		"It keeps running after you close this app. /discord shows it, /discord stop stops it.",
	}, "\n")
}

func discordOfferText() string {
	return strings.Join([]string{
		"Discord is installed on this PC — GAIA can answer you there too.",
		"",
		"DM GAIA from your phone and it answers from here, with access to your",
		"files. Setup takes about two minutes.",
		"",
		"  /discord setup   connect it now",
		"  /discord skip    not now",
		"  /discord never   stop asking",
	}, "\n")
}

func discordHelpText() string {
	return strings.Join([]string{
		"Discord commands:",
		"  /discord             status and what to do next",
		"  /discord setup       connect a bot (opens each page for you)",
		"  /discord start       start answering",
		"  /discord stop        stop answering",
		"  /discord restart     restart the bridge",
		"  /discord takeover    answer from this PC instead of another one",
		"  /discord pair        pair a different Discord account",
		"  /discord disconnect  forget the bot and the pairing",
	}, "\n")
}

// discordStatusText is /discord's answer: the state and the one next step.
func discordStatusText(s gaiadiscord.Status) string {
	lines := []string{"Discord: " + s.Summary()}
	if s.Detail != "" && s.State != gaiadiscord.StateConnected {
		lines = append(lines, s.Detail)
	}
	lines = append(lines, "")
	switch {
	case !s.Connected:
		lines = append(lines, "Type /discord setup to connect a bot — it opens each page for you.")
	case s.State == gaiadiscord.StateConnected:
		lines = append(lines,
			"DM "+s.BotName+" in Discord: "+s.BotProfileURL,
			"/discord stop · /discord pair (another account) · /discord disconnect")
	case s.State == gaiadiscord.StateTakenOver:
		lines = append(lines, "Type /discord takeover to answer from this PC instead.")
	case s.State == gaiadiscord.StateTokenBad:
		lines = append(lines, "Type /discord setup to paste a new token.")
	case s.State == gaiadiscord.StateConflict:
		lines = append(lines,
			"Create a second application in the Developer Portal, then type /discord setup",
			"and paste its token: "+s.PortalURL)
	case s.State == gaiadiscord.StateCredentials:
		lines = append(lines, "Unlock your keyring, then type /discord restart.")
	default:
		lines = append(lines, "Type /discord start to start answering.")
	}
	return strings.Join(lines, "\n")
}

func discordUnavailableNote(err error) string {
	if errors.Is(err, gaiadiscord.ErrUnanswered) {
		return fmt.Sprintf("Could not check Discord: %v\n\nThis says nothing about whether "+
			"Discord is set up — only that the question could not be asked.", err)
	}
	return fmt.Sprintf("Could not check Discord: %v", err)
}
