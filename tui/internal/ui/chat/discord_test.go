// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/gaiadiscord"
)

func pairingStatus(code string) gaiadiscord.Status {
	return gaiadiscord.Status{
		Connected:     true,
		State:         gaiadiscord.StatePairing,
		BotName:       "gaia",
		InviteURL:     "https://discord.com/oauth2/authorize?client_id=1&scope=bot&permissions=0",
		BotProfileURL: "https://discord.com/users/9",
		PairingCode:   code,
	}
}

func TestDiscordSetupOpensThePortalAndAsksForAMaskedToken(t *testing.T) {
	m := slackModel(t)
	updated, cmd := m.handleDiscordCommand("/discord setup")
	after := updated.(ChatModel)
	if cmd == nil {
		t.Error("setup must open the Developer Portal")
	}
	if after.question == nil || !isDiscordSetupQuestion(after.question.RequestID()) {
		t.Fatal("setup must ask for the token inside the TUI")
	}
	if !after.question.Sensitive() {
		t.Error("the token prompt must be masked")
	}
	note := lastStatus(t, after)
	for _, want := range []string{gaiadiscord.PortalURL, "New Application", "Reset Token"} {
		if !strings.Contains(note, want) {
			t.Errorf("setup text lacks %q:\n%s", want, note)
		}
	}
	if strings.Contains(note, "gaia discord") {
		t.Error("setup must not tell the user to run a command")
	}
}

func TestAnEmptyTokenIsAskedForAgain(t *testing.T) {
	m := slackModel(t)
	m.discordSetup = &discordSetupState{}
	updated, cmd := m.handleDiscordSetupAnswer(discordQuestionPrefix+"token", "   ")
	after := updated.(ChatModel)
	if cmd != nil {
		t.Error("an empty token must not be sent anywhere")
	}
	if after.question == nil || !after.question.Sensitive() {
		t.Fatal("the masked prompt must come back")
	}
}

func TestTheTokenNeverLandsInTheTranscript(t *testing.T) {
	m := slackModel(t)
	m.discordSetup = &discordSetupState{}
	updated, cmd := m.handleDiscordSetupAnswer(discordQuestionPrefix+"token", "MTU.secret.token")
	if cmd == nil {
		t.Fatal("a token must be handed to connect")
	}
	for _, msg := range updated.(ChatModel).messages {
		if strings.Contains(msg.Content, "secret") {
			t.Fatalf("the token reached the transcript: %q", msg.Content)
		}
	}
}

func TestConnectedSetupShowsTheInviteAndTheCode(t *testing.T) {
	m := slackModel(t)
	m.discordSetup = &discordSetupState{}
	updated, cmd := m.handleDiscordConnected(discordConnectedMsg{
		result: gaiadiscord.ConnectResult{
			Connected: true, BotName: "gaia", BotPublic: true,
			InviteURL:  "https://discord.com/oauth2/authorize?client_id=1&scope=bot&permissions=0",
			BotPageURL: "https://discord.com/developers/applications/1/bot",
		},
		status: pairingStatus("ABCD2345"),
	})
	after := updated.(ChatModel)
	if cmd == nil {
		t.Error("the invite page must open, and pairing must be watched")
	}
	all := ""
	for _, msg := range after.messages {
		all += msg.Content + "\n"
	}
	for _, want := range []string{"ABCD-2345", "client_id=1", "discord.com/users/9", "Public Bot", "Create My Own"} {
		if !strings.Contains(all, want) {
			t.Errorf("setup output lacks %q:\n%s", want, all)
		}
	}
	if after.discordSetup == nil || after.discordSetup.pollUntil.IsZero() {
		t.Error("the pairing step must be watched")
	}
}

func TestAFailedConnectSaysWhyAndHowToRetry(t *testing.T) {
	m := slackModel(t)
	m.discordSetup = &discordSetupState{}
	updated, _ := m.handleDiscordConnected(discordConnectedMsg{err: errors.New("Discord refused that token.")})
	note := lastStatus(t, updated.(ChatModel))
	if !strings.Contains(note, "Discord refused that token") || !strings.Contains(note, "/discord setup") {
		t.Errorf("note %q", note)
	}
	if updated.(ChatModel).discordSetup != nil {
		t.Error("a failed setup must close")
	}
}

func TestPairingIsAnnouncedWhenItHappens(t *testing.T) {
	m := slackModel(t)
	m.discordSetup = &discordSetupState{shownCode: "ABCD2345", pollUntil: time.Now().Add(time.Minute)}
	paired := gaiadiscord.Status{
		Connected: true, Paired: true, State: gaiadiscord.StateConnected,
		BotName: "gaia", OwnerName: "kalin", Model: "fireworks.deepseek-v4p1-flash",
	}
	updated, cmd := m.handleDiscordPoll(discordPollMsg{status: paired})
	after := updated.(ChatModel)
	if cmd != nil {
		t.Error("polling must stop once paired")
	}
	if note := lastStatus(t, after); !strings.Contains(note, "Paired with kalin") {
		t.Errorf("note %q", note)
	}
	if after.discordSetup != nil {
		t.Error("setup must close once paired")
	}
}

func TestARotatedCodeIsShown(t *testing.T) {
	m := slackModel(t)
	m.discordSetup = &discordSetupState{shownCode: "ABCD2345", pollUntil: time.Now().Add(time.Minute)}
	updated, cmd := m.handleDiscordPoll(discordPollMsg{status: pairingStatus("WXYZ6789")})
	if cmd == nil {
		t.Error("polling must continue")
	}
	if note := lastStatus(t, updated.(ChatModel)); !strings.Contains(note, "WXYZ-6789") {
		t.Errorf("note %q", note)
	}
}

func TestStatusNamesTheNextStepWithoutACommandLine(t *testing.T) {
	cases := map[string]gaiadiscord.Status{
		"/discord setup":    {},
		"/discord takeover": {Connected: true, State: gaiadiscord.StateTakenOver},
		"/discord restart":  {Connected: true, State: gaiadiscord.StateCredentials},
		"/discord start":    {Connected: true, State: gaiadiscord.StateStopped},
	}
	for want, s := range cases {
		text := discordStatusText(s)
		if !strings.Contains(text, want) {
			t.Errorf("state %q: %q lacks %q", s.State, text, want)
		}
		if strings.Contains(text, "gaia discord") {
			t.Errorf("state %q tells the user to run a command: %q", s.State, text)
		}
	}
}

func TestTheOfferAppearsOnceAndOnlyWhenAsked(t *testing.T) {
	m := slackModel(t)
	updated, _ := m.handleDiscordStatus(discordStatusMsg{status: gaiadiscord.Status{}, offer: true})
	if hasStatus(updated.(ChatModel)) {
		t.Error("no offer unless the CLI says so")
	}
	offer := discordStatusMsg{status: gaiadiscord.Status{ShouldOfferSetup: true}, offer: true}
	updated, _ = m.handleDiscordStatus(offer)
	after := updated.(ChatModel)
	if !strings.Contains(lastStatus(t, after), "/discord setup") {
		t.Error("the offer must name /discord setup")
	}
	before := len(after.messages)
	again, _ := after.handleDiscordStatus(offer)
	if len(again.(ChatModel).messages) != before {
		t.Error("the offer must not repeat in one session")
	}
}
