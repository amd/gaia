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

// ----------------------------------------------------------------------
// Setup runs inside the TUI
// ----------------------------------------------------------------------

func TestSetupAsksInsideTheTUIRatherThanHandingOverTheTerminal(t *testing.T) {
	// tea.ExecProcess was the first implementation and it is wrong: a suspended
	// TUI cannot be drawn, cannot be driven by the control API, and cannot be
	// exercised end to end. It also reads as the app crashing and returning.
	m := slackModel(t)
	// The real sequence: /slack setup opens the flow, then the URL arrives.
	m.slackSetup = &slackSetupState{step: slackStepConfirmCreated}
	updated, _ := m.handleSlackURL(slackURLMsg{url: "https://api.slack.com/apps?x=1"})
	after := updated.(ChatModel)

	if after.question == nil {
		t.Fatal("setup must put its question up in the TUI")
	}
	if !isSlackSetupQuestion(after.question.RequestID()) {
		t.Errorf("question %q is not owned by the Slack flow", after.question.RequestID())
	}
	if !strings.Contains(lastStatus(t, after), "api.slack.com/apps") {
		t.Error("the create-app URL must be shown, not only opened in a browser")
	}
}

func TestTheTokenPromptsAreMasked(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepAppToken}

	updated, _ := m.handleSlackSetupAnswer(slackQuestionPrefix+"created", "yes")
	after := updated.(ChatModel)

	if after.question == nil {
		t.Fatal("a token prompt must follow")
	}
	// Sensitive:true is what stops a pasted token being echoed into a
	// scrollback the user may later screen-share.
	if !after.question.Sensitive() {
		t.Error("a token prompt must be masked")
	}
}

func TestCancellingStoresNothing(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepConfirmCreated}

	updated, cmd := m.handleSlackSetupAnswer(slackQuestionPrefix+"created", "cancel")
	after := updated.(ChatModel)

	if after.slackSetup != nil {
		t.Error("cancelling must end the flow")
	}
	if cmd != nil {
		t.Error("cancelling must not run anything")
	}
	if !strings.Contains(lastStatus(t, after), "nothing was stored") {
		t.Errorf("the user must be told nothing was stored, got: %s", lastStatus(t, after))
	}
}

func TestAnEmptyTokenReAsksInsteadOfStoringIt(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepAppToken}

	updated, cmd := m.handleSlackSetupAnswer(slackQuestionPrefix+"app-token", "   ")
	after := updated.(ChatModel)

	if cmd != nil {
		t.Error("an empty token must not be sent anywhere")
	}
	if after.question == nil || !strings.Contains(after.question.Prompt(), "empty") {
		t.Error("the prompt must say it was empty and ask again")
	}
}

func TestTheAppTokenIsDroppedFromUIStateOnceHandedOff(t *testing.T) {
	// A secret sitting in model state outlives the screen it was typed on.
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepBotToken, appToken: "xapp-secret"}

	updated, cmd := m.handleSlackSetupAnswer(slackQuestionPrefix+"bot-token", "xoxb-secret")
	after := updated.(ChatModel)

	if cmd == nil {
		t.Fatal("both tokens collected — the connect must run")
	}
	if after.slackSetup.appToken != "" {
		t.Error("the app token must not stay in UI state after hand-off")
	}
}

func TestNoTokenEverReachesTheTranscript(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepAppToken}

	updated, _ := m.handleSlackSetupAnswer(slackQuestionPrefix+"app-token", "xapp-super-secret")
	after := updated.(ChatModel)

	for _, msg := range after.messages {
		if strings.Contains(msg.Content, "super-secret") {
			t.Fatalf("a token reached the transcript: %q", msg.Content)
		}
	}
}

func TestAFailedConnectSaysHowToRetry(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepConnecting}

	updated, _ := m.handleSlackConnected(slackConnectedMsg{
		err: errors.New("Slack rejected the tokens"),
	})
	after := updated.(ChatModel)

	text := lastStatus(t, after)
	if !strings.Contains(text, "Slack rejected the tokens") {
		t.Errorf("the real reason must reach the user, got: %s", text)
	}
	if !strings.Contains(text, "/slack setup") {
		t.Errorf("a failure must name the retry, got: %s", text)
	}
	if after.slackSetup != nil {
		t.Error("a failed flow must not stay open")
	}
}

func TestASuccessfulConnectNamesTheWorkspaceAndTheNextStep(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepConnecting}

	updated, _ := m.handleSlackConnected(slackConnectedMsg{team: "Acme"})
	text := lastStatus(t, updated.(ChatModel))

	if !strings.Contains(text, "Acme") {
		t.Errorf("the workspace must be named, got: %s", text)
	}
	// Starting without an allowlist is refused, so the next step has to be here.
	if !strings.Contains(text, "--allowed-users") {
		t.Errorf("the next step must be spelled out, got: %s", text)
	}
}

func TestAFailedURLFetchDoesNotLeaveTheFlowOpen(t *testing.T) {
	m := slackModel(t)
	m.slackSetup = &slackSetupState{step: slackStepConfirmCreated}

	updated, _ := m.handleSlackURL(slackURLMsg{err: errors.New("gaia is not on PATH")})
	after := updated.(ChatModel)

	if after.slackSetup != nil {
		t.Error("a flow that cannot start must not stay open")
	}
	if !strings.Contains(lastStatus(t, after), "not on PATH") {
		t.Error("the reason must be shown")
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
