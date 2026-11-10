// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

// ErrNoPlayer is returned on Linux when no supported audio player is on PATH.
var ErrNoPlayer = errors.New("reading aloud needs an audio player and none was found: " +
	"audio playback needs PipeWire (pw-play), PulseAudio (paplay) or ALSA's player (aplay)")

// Player plays one WAV file with the operating system's own player, blocking
// until it finishes. Cancelling the context kills the player.
type Player struct {
	name string
	argv func(path string) []string
}

// Name is the program that plays the audio.
func (p Player) Name() string { return p.name }

// Argv is the command line that plays path.
func (p Player) Argv(path string) []string { return p.argv(path) }

// FindPlayer picks this machine's audio player.
func FindPlayer() (Player, error) { return findPlayer(runtime.GOOS, exec.LookPath) }

func findPlayer(goos string, lookPath func(string) (string, error)) (Player, error) {
	switch goos {
	case "windows":
		exe, err := lookPath("powershell")
		if err != nil {
			return Player{}, fmt.Errorf("reading aloud plays audio through Windows PowerShell, which was not found: %w", err)
		}
		return Player{name: "PowerShell", argv: func(path string) []string {
			return []string{exe, "-NoProfile", "-NonInteractive", "-Command",
				"(New-Object Media.SoundPlayer " + powershellQuote(path) + ").PlaySync()"}
		}}, nil
	case "darwin":
		exe, err := lookPath("afplay")
		if err != nil {
			return Player{}, fmt.Errorf("reading aloud plays audio with macOS's afplay, which was not found: %w", err)
		}
		return Player{name: "afplay", argv: func(path string) []string { return []string{exe, path} }}, nil
	}
	for _, name := range []string{"pw-play", "paplay", "aplay"} {
		if exe, err := lookPath(name); err == nil {
			return Player{name: name, argv: func(path string) []string { return []string{exe, path} }}, nil
		}
	}
	return Player{}, ErrNoPlayer
}

// powershellQuote makes path one single-quoted PowerShell string, where the
// only special character is the quote itself, written twice.
func powershellQuote(path string) string {
	return "'" + strings.ReplaceAll(path, "'", "''") + "'"
}

// Play plays the WAV file at path and returns when it has finished.
func (p Player) Play(ctx context.Context, path string) error {
	argv := p.argv(path)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	var out bytes.Buffer
	// Never the terminal: the TUI owns it.
	cmd.Stdout = &out
	cmd.Stderr = &out
	cmd.WaitDelay = 2 * time.Second
	hideWindow(cmd)
	err := cmd.Run()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err != nil {
		detail := strings.TrimSpace(out.String())
		if detail != "" {
			return fmt.Errorf("%s could not play the audio (%v): %s", p.name, err, clipText(detail, 300))
		}
		return fmt.Errorf("%s could not play the audio: %v", p.name, err)
	}
	return nil
}

func clipText(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n-1]) + "…"
}
