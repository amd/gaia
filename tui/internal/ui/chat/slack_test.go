// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/gaiaslack"
)

func slackModel(t *testing.T) ChatModel {
	t.Helper()
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 100, 30
	return m
}

// lastStatus returns the text of the most recent status message.
func lastStatus(t *testing.T, m ChatModel) string {
	t.Helper()
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleStatus {
			return m.messages[i].Content
		}
	}
	t.Fatal("no status message was added")
	return ""
}

func hasStatus(m ChatModel) bool {
	for _, msg := range m.messages {
		if msg.Role == RoleStatus {
			return true
		}
	}
	return false
}

// ----------------------------------------------------------------------
// The one-time offer
// ----------------------------------------------------------------------

func TestOfferAppearsWhenTheCLISaysSo(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleSlackStatus(slackStatusMsg{
		status: gaiaslack.Status{SlackInstalled: true, ShouldOfferSetup: true},
		offer:  true,
	})
	text := lastStatus(t, updated.(ChatModel))
	for _, want := range []string{"/slack setup", "/slack skip", "/slack never"} {
		if !strings.Contains(text, want) {
			t.Errorf("the offer must show %q, got:\n%s", want, text)
		}
	}
}

func TestOfferStaysHiddenWhenTheCLISaysNotToShowIt(t *testing.T) {
	// The rule — offer once, then only if something changed — lives in Python.
	// The TUI must not second-guess it from SlackInstalled alone.
	m := slackModel(t)
	updated, _ := m.handleSlackStatus(slackStatusMsg{
		status: gaiaslack.Status{SlackInstalled: true, ShouldOfferSetup: false},
		offer:  true,
	})
	if hasStatus(updated.(ChatModel)) {
		t.Errorf("nothing should have been shown, got:\n%s",
			lastStatus(t, updated.(ChatModel)))
	}
}

func TestOfferIsNotRepeatedInOneSession(t *testing.T) {
	m := slackModel(t)
	msg := slackStatusMsg{
		status: gaiaslack.Status{SlackInstalled: true, ShouldOfferSetup: true},
		offer:  true,
	}
	first, _ := m.handleSlackStatus(msg)
	second, _ := first.(ChatModel).handleSlackStatus(msg)

	count := 0
	for _, message := range second.(ChatModel).messages {
		if message.Role == RoleStatus && strings.Contains(message.Content, "/slack setup") {
			count++
		}
	}
	if count != 1 {
		t.Errorf("the offer appeared %d times, want 1", count)
	}
}

func TestAFailedProbeAtLaunchStaysSilent(t *testing.T) {
	// The user did not ask for this check; interrupting them with its failure
	// would be noise. /slack still reports it on demand.
	m := slackModel(t)
	updated, _ := m.handleSlackStatus(slackStatusMsg{
		err:   fmt.Errorf("%w: gaia is not on PATH", gaiaslack.ErrUnanswered),
		offer: true,
	})
	if hasStatus(updated.(ChatModel)) {
		t.Errorf("a background failure must not be shown, got:\n%s",
			lastStatus(t, updated.(ChatModel)))
	}
}

// ----------------------------------------------------------------------
// /slack
// ----------------------------------------------------------------------

func TestSlackCommandReportsAFailureTheUserAskedFor(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleSlackStatus(slackStatusMsg{
		err:   fmt.Errorf("%w: gaia is not on PATH", gaiaslack.ErrUnanswered),
		offer: false,
	})
	text := lastStatus(t, updated.(ChatModel))
	if !strings.Contains(text, "not on PATH") {
		t.Errorf("the real reason must be quoted, got:\n%s", text)
	}
	if !strings.Contains(text, "says nothing about whether Slack is set up") {
		t.Errorf("an unanswered probe must not read as 'not connected', got:\n%s", text)
	}
}

func TestStatusTextTellsAConnectedUserWhatToDoNext(t *testing.T) {
	text := slackStatusText(gaiaslack.Status{Configured: true, TeamName: "Acme"})
	if !strings.Contains(text, "gaia slack start") {
		t.Errorf("a configured-but-stopped bridge must name the start command, got:\n%s", text)
	}
	if !strings.Contains(text, "--allowed-users") {
		t.Errorf("the start command must carry the allowlist flag, got:\n%s", text)
	}
}

func TestStatusTextTellsARunningUserToJustMessageIt(t *testing.T) {
	text := slackStatusText(gaiaslack.Status{
		Configured: true, Running: true, TeamName: "Acme",
	})
	if !strings.Contains(text, "direct message") {
		t.Errorf("a running bridge must say how to use it, got:\n%s", text)
	}
}

func TestStatusTextOffersSetupWhenNothingIsConfigured(t *testing.T) {
	text := slackStatusText(gaiaslack.Status{SlackInstalled: true})
	if !strings.Contains(text, gaiaslack.TypedCommand) {
		t.Errorf("an unconfigured machine must name the setup command, got:\n%s", text)
	}
}

// ----------------------------------------------------------------------
// Recording the answer
// ----------------------------------------------------------------------

func TestSkipAndNeverReadDifferently(t *testing.T) {
	m := slackModel(t)
	skipped, _ := m.handleSlackDeclined(slackDeclinedMsg{never: false})
	never, _ := m.handleSlackDeclined(slackDeclinedMsg{never: true})

	skipText := lastStatus(t, skipped.(ChatModel))
	neverText := lastStatus(t, never.(ChatModel))
	if skipText == neverText {
		t.Fatal("'not now' and 'stop asking' must not read identically")
	}
	if !strings.Contains(neverText, "again") {
		t.Errorf("never must say it is permanent, got %q", neverText)
	}
	for _, text := range []string{skipText, neverText} {
		if !strings.Contains(text, "/slack") {
			t.Errorf("every decline must leave a way back, got %q", text)
		}
	}
}

func TestAFailedDeclineNamesTheManualCommand(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleSlackDeclined(slackDeclinedMsg{
		err: errors.New("keyring is locked"),
	})
	text := lastStatus(t, updated.(ChatModel))
	if !strings.Contains(text, "keyring is locked") {
		t.Errorf("the real reason must be quoted, got:\n%s", text)
	}
	if !strings.Contains(text, "gaia slack decline") {
		t.Errorf("a failure must name what to run instead, got:\n%s", text)
	}
}

// ----------------------------------------------------------------------
// Setup hand-over
// ----------------------------------------------------------------------

func TestSetupSuccessReprobesRatherThanClaimingSuccess(t *testing.T) {
	// The user can quit setup half-way. Saying "connected" when no token was
	// stored is worse than saying nothing.
	m := slackModel(t)
	updated, cmd := m.handleSlackSetupDone(slackSetupDoneMsg{})
	if cmd == nil {
		t.Fatal("a finished setup must re-probe")
	}
	text := lastStatus(t, updated.(ChatModel))
	if strings.Contains(strings.ToLower(text), "connected") {
		t.Errorf("success must not be claimed before it is checked, got %q", text)
	}
}

func TestAFailedSetupNamesTheRetryCommand(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleSlackSetupDone(slackSetupDoneMsg{
		err: errors.New("exit status 2"),
	})
	text := lastStatus(t, updated.(ChatModel))
	if !strings.Contains(text, gaiaslack.TypedCommand) {
		t.Errorf("a failure must name the retry command, got:\n%s", text)
	}
}

// ----------------------------------------------------------------------
// Command routing
// ----------------------------------------------------------------------

func TestEverySlackCommandIsHandledRatherThanSentToTheAgent(t *testing.T) {
	// A slash command that falls through reaches the LLM as a question, and the
	// model cheerfully answers it as prose — the failure /setup exists to avoid.
	for _, command := range []string{"/slack", "/slack setup", "/slack skip", "/slack never"} {
		m := slackModel(t)
		updated, _ := m.submit(command)
		after := updated.(ChatModel)
		for _, message := range after.messages {
			if message.Role == RoleUser {
				t.Errorf("%q reached the agent as a question", command)
			}
		}
	}
}

func TestSlackIsOfferedInTheCommandPalette(t *testing.T) {
	for _, entry := range paletteCommands {
		if entry.Name == "/slack" {
			if entry.Desc == "" {
				t.Error("/slack needs a description in the palette")
			}
			return
		}
	}
	t.Error("/slack is missing from the command palette")
}
