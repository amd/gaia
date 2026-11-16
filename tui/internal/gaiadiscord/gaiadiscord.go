// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiadiscord drives the `gaia discord` CLI for the TUI.
//
// Like gaiaslack it re-derives nothing: src/gaia/messaging/discord/ owns what
// "connected", "paired" and "running" mean, and every call here is a thin
// reader of the CLI's JSON. The bot token goes to the child on STDIN, never in
// argv, where every process on the machine could read it.
package gaiadiscord

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"

	"github.com/amd/gaia/tui/internal/gaiainit"
)

// CallTimeout bounds a status probe or a connect. Starting the bridge waits
// for it to reach Discord, so StartTimeout is longer.
const (
	CallTimeout  = 45 * time.Second
	StartTimeout = 90 * time.Second
)

// ErrUnanswered wraps every failure to ASK, as opposed to a clean "not
// connected" answer, so a caller never renders a broken probe as a fact.
var ErrUnanswered = errors.New("Discord status could not be determined")

// Binary resolves the `gaia` CLI; a var so tests can substitute a stub.
var Binary = func() (string, error) {
	bin, err := gaiainit.ResolveCLI()
	if err != nil {
		return "", fmt.Errorf("the `gaia` command is missing from this install, " +
			"so Discord cannot be set up. Reinstall GAIA from https://amd-gaia.ai/#install")
	}
	return bin, nil
}

// Bridge states, as `gaia discord status --json` reports them.
const (
	StatePairing     = "pairing"
	StateConnected   = "connected"
	StateStarting    = "starting"
	StateTakenOver   = "taken_over"
	StateTokenBad    = "token_invalid"
	StateCredentials = "credentials_unavailable"
	StateConflict    = "conflict"
	StateStopped     = "stopped"
	StateError       = "error"
)

// PortalURL is where a Discord application and its bot are created.
const PortalURL = "https://discord.com/developers/applications"

// Status is `gaia discord status --json`.
type Status struct {
	Connected        bool    `json:"connected"`
	Paired           bool    `json:"paired"`
	Running          bool    `json:"running"`
	State            string  `json:"state"`
	Detail           string  `json:"detail"`
	BotName          string  `json:"bot_name"`
	OwnerName        string  `json:"owner_name"`
	Model            string  `json:"model"`
	PortalURL        string  `json:"portal_url"`
	BotPageURL       string  `json:"bot_page_url"`
	InviteURL        string  `json:"invite_url"`
	BotProfileURL    string  `json:"bot_profile_url"`
	PairingCode      string  `json:"pairing_code"`
	PairingExpiresAt float64 `json:"pairing_expires_at"`
	DiscordInstalled bool    `json:"discord_installed"`
	ShouldOfferSetup bool    `json:"should_offer_setup"`
}

// Summary is the one-line form for a status row.
func (s Status) Summary() string {
	switch {
	case !s.Connected:
		return "not connected"
	case s.State == StateConnected:
		return fmt.Sprintf("answering %s as %s", s.OwnerName, s.BotName)
	case s.State == StatePairing:
		return fmt.Sprintf("%s is waiting to be paired", s.BotName)
	case s.State == StateTakenOver:
		return "another PC is answering this bot"
	case s.State == StateTokenBad:
		return "Discord refused the bot token"
	case s.State == StateCredentials:
		return "the bot token could not be read from the OS keyring"
	case s.State == StateConflict:
		return "this bot is also the Discord connector's bot"
	case s.State == StateError:
		return "the bridge stopped with an error"
	default:
		return fmt.Sprintf("connected as %s, not running", s.BotName)
	}
}

// ConnectResult is what `gaia discord connect` prints.
type ConnectResult struct {
	Connected     bool   `json:"connected"`
	Error         string `json:"error"`
	BotName       string `json:"bot_name"`
	InviteURL     string `json:"invite_url"`
	BotPageURL    string `json:"bot_page_url"`
	BotPublic     bool   `json:"bot_public"`
	ApplicationID string `json:"application_id"`
}

func run(ctx context.Context, timeout time.Duration, stdin string, args ...string) ([]byte, string, error) {
	bin, err := Binary()
	if err != nil {
		return nil, "", err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, append([]string{"discord"}, args...)...)
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	var out, errBuf bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errBuf
	runErr := cmd.Run()
	return out.Bytes(), lastMeaningfulLine(errBuf.String() + out.String()), runErr
}

// jsonFrom decodes the last JSON object a command printed: a stray log line
// before it must not break the decode.
func jsonFrom(raw []byte, into any) error {
	i := bytes.LastIndex(raw, []byte("\n{"))
	if i >= 0 {
		raw = raw[i+1:]
	} else if j := bytes.IndexByte(raw, '{'); j > 0 {
		raw = raw[j:]
	}
	return json.Unmarshal(bytes.TrimSpace(raw), into)
}

// Query reads the current status without changing anything.
func Query(ctx context.Context) (Status, error) {
	out, said, err := run(ctx, CallTimeout, "", "status", "--json")
	if err != nil {
		return Status{}, fmt.Errorf("%w (%w). GAIA said: %s", ErrUnanswered, err, said)
	}
	var s Status
	if err := json.Unmarshal(bytes.TrimSpace(out[max(0, bytes.IndexByte(out, '{')):]), &s); err != nil {
		return Status{}, fmt.Errorf("%w: the status was not JSON (%w): %s", ErrUnanswered, err, said)
	}
	return s, nil
}

// Connect validates and stores a bot token the caller collected.
func Connect(ctx context.Context, token string) (ConnectResult, error) {
	out, said, runErr := run(ctx, CallTimeout, strings.TrimSpace(token)+"\n", "connect")
	var r ConnectResult
	if err := jsonFrom(out, &r); err != nil {
		return ConnectResult{}, fmt.Errorf("Discord setup said something unexpected: %s", said)
	}
	if !r.Connected {
		if r.Error == "" {
			r.Error = said
		}
		return ConnectResult{}, errors.New(r.Error)
	}
	if runErr != nil {
		return ConnectResult{}, fmt.Errorf("Discord setup failed: %s", said)
	}
	return r, nil
}

// statusCall runs a command that ends by printing the status JSON.
func statusCall(ctx context.Context, timeout time.Duration, args ...string) (Status, error) {
	out, said, runErr := run(ctx, timeout, "", args...)
	var s Status
	if err := jsonFrom(out, &s); err != nil {
		if runErr != nil {
			return Status{}, fmt.Errorf("%s", said)
		}
		return Status{}, fmt.Errorf("Discord said something unexpected: %s", said)
	}
	return s, nil
}

// Start starts the bridge on its own (it outlives the TUI) and returns its
// status once it has reached Discord. model is the chat model to answer with;
// empty keeps the one it last used.
func Start(ctx context.Context, model string) (Status, error) {
	args := []string{"start", "--detach"}
	if model != "" {
		args = append(args, "--model", model)
	}
	return statusCall(ctx, StartTimeout, args...)
}

// Restart stops and starts the bridge; takeOver answers from this PC even
// when another PC holds the lease.
func Restart(ctx context.Context, model string, takeOver bool) (Status, error) {
	args := []string{"restart"}
	if model != "" {
		args = append(args, "--model", model)
	}
	if takeOver {
		args = append(args, "--take-over")
	}
	return statusCall(ctx, StartTimeout, args...)
}

// Pair forgets the paired account and starts over with a new code.
func Pair(ctx context.Context) (Status, error) {
	return statusCall(ctx, StartTimeout, "pair")
}

// Autostart starts a connected bridge that is set to autostart and is not
// running. Nothing happens otherwise.
func Autostart(ctx context.Context) error {
	_, said, err := run(ctx, StartTimeout, "", "autostart")
	if err != nil {
		return fmt.Errorf("%s", said)
	}
	return nil
}

// simple runs a command whose only output is prose.
func simple(ctx context.Context, what string, args ...string) error {
	_, said, err := run(ctx, CallTimeout, "", args...)
	if err != nil {
		return fmt.Errorf("could not %s: %s", what, said)
	}
	return nil
}

// Stop stops the bridge.
func Stop(ctx context.Context) error { return simple(ctx, "stop Discord", "stop") }

// Disconnect stops the bridge and forgets the token and the pairing.
func Disconnect(ctx context.Context) error {
	return simple(ctx, "disconnect Discord", "disconnect")
}

// Decline records "not now", or with never, "stop asking".
func Decline(ctx context.Context, never bool) error {
	args := []string{"decline"}
	if never {
		args = append(args, "--never")
	}
	return simple(ctx, "record that", args...)
}

func lastMeaningfulLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if line := strings.TrimSpace(lines[i]); line != "" {
			return line
		}
	}
	return "(no output)"
}
