// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package gaiaautomation drives the `gaia automation` CLI for the TUI.
//
// It re-derives nothing: src/gaia/automations/ owns the store, and every call
// here reads the CLI's JSON or relays its one-line answer.
package gaiaautomation

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

// CallTimeout bounds one list or change; a cold `gaia` start alone can take ~15 s.
const CallTimeout = 45 * time.Second

// ErrUnanswered wraps every failure to ASK, so a broken probe is never shown
// as "no automations".
var ErrUnanswered = errors.New("the automations could not be read")

// Binary resolves the `gaia` CLI; a var so tests can substitute a stub.
var Binary = func() (string, error) {
	bin, err := gaiainit.ResolveCLI()
	if err != nil {
		return "", fmt.Errorf("the `gaia` command is missing from this install, " +
			"so automations cannot be shown. Reinstall GAIA from https://amd-gaia.ai/#install")
	}
	return bin, nil
}

// KindWatch is a Discord GitHub watch, which `gaia automation list` includes
// but which these commands do not change.
const KindWatch = "watch"

// KindWaiting is a message an automation already produced that waits for the
// Discord bridge; it shares its automation's name, so it is left out too.
const KindWaiting = "waiting"

// Item is one row of `gaia automation list` (store.describe).
type Item struct {
	ID            string `json:"id"`
	Name          string `json:"name"`
	Kind          string `json:"kind"`
	Status        string `json:"status"`
	Next          string `json:"next"`
	Every         string `json:"every"`
	QuietHours    string `json:"quiet_hours"`
	Days          string `json:"days"`
	Says          string `json:"says"`
	Prompt        string `json:"prompt"`
	Check         string `json:"check"`
	FailingChecks int    `json:"failing_checks"`
}

// Paused reports whether the item is paused.
func (i Item) Paused() bool { return i.Status == "paused" }

// Heartbeat reports whether the item is the user's check-in alert.
func (i Item) Heartbeat() bool {
	return i.Kind == "alert" && strings.EqualFold(strings.TrimSpace(i.Name), "heartbeat")
}

func run(ctx context.Context, args ...string) ([]byte, string, error) {
	bin, err := Binary()
	if err != nil {
		return nil, "", err
	}
	ctx, cancel := context.WithTimeout(ctx, CallTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, append([]string{"automation"}, args...)...)
	var out, errBuf bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errBuf
	runErr := cmd.Run()
	return out.Bytes(), lastMeaningfulLine(errBuf.String() + out.String()), runErr
}

// List reads every automation, the Discord watches and waiting messages left out.
func List(ctx context.Context) ([]Item, error) {
	out, said, err := run(ctx, "list")
	if err != nil {
		return nil, fmt.Errorf("%w (%w). GAIA said: %s", ErrUnanswered, err, said)
	}
	// The list is the last thing printed; a stray line before it may hold a "[".
	start := bytes.LastIndex(out, []byte("\n["))
	if start >= 0 {
		start++
	} else if start = bytes.IndexByte(out, '['); start < 0 {
		return nil, fmt.Errorf("%w: the list was not JSON: %s", ErrUnanswered, said)
	}
	var all []Item
	if err := json.Unmarshal(bytes.TrimSpace(out[start:]), &all); err != nil {
		return nil, fmt.Errorf("%w: the list was not JSON (%w): %s", ErrUnanswered, err, said)
	}
	items := make([]Item, 0, len(all))
	for _, it := range all {
		if it.Kind != KindWatch && it.Kind != KindWaiting {
			items = append(items, it)
		}
	}
	return items, nil
}

// Actions these commands take.
const (
	ActionPause  = "pause"
	ActionResume = "resume"
	ActionRemove = "remove"
)

// Set pauses, resumes or removes the automation with this id or exact name,
// and returns the CLI's confirmation line.
func Set(ctx context.Context, action, ref string) (string, error) {
	switch action {
	case ActionPause, ActionResume, ActionRemove:
	default:
		return "", fmt.Errorf("unknown automation action %q", action)
	}
	// "--" ends the flags: a ref like "--all" is a name to look up, never
	// `remove --all`, which deletes every automation.
	_, said, err := run(ctx, action, "--", ref)
	if err != nil {
		return "", errors.New(strings.TrimPrefix(said, "❌ "))
	}
	return said, nil
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
