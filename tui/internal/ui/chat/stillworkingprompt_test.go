// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"
)

// "still working — usually 60-90s" reassures a user watching a slow model. On
// a turn parked on the user's own answer it says the opposite of the truth:
// nothing is working, the agent is waiting on them. Asserted on the painted
// frame, for the reason confirmvisible_test.go gives.

// slowTurn is a streaming turn well past stillWorkingAfter with nothing done.
func slowTurn(t *testing.T) ChatModel {
	t.Helper()
	m, _ := liveModel(t)
	m.resize()
	m.streaming = true
	m.queryStart = time.Now().Add(-2 * stillWorkingAfter)
	return m
}

func TestAPendingPromptNeverShowsTheStillWorkingHint(t *testing.T) {
	for name, pending := range map[string]interface{}{
		"confirmation": gatedShellCall(),
		"question":     needsInput(),
	} {
		t.Run(name, func(t *testing.T) {
			m := feed(t, slowTurn(t), pending)
			m.updateViewport()

			frame := ansi.Strip(visibleFrame(m))
			if strings.Contains(frame, "still working") {
				t.Errorf("a %s is waiting on the user, but the frame says the agent is still working:\n%s",
					name, frame)
			}
		})
	}
}

// The suppression is scoped to the prompt: a genuinely slow first step with
// nothing asked of the user still gets the reassurance.
func TestASlowFirstStepStillShowsTheHint(t *testing.T) {
	m := slowTurn(t)
	m.updateViewport()

	frame := ansi.Strip(visibleFrame(m))
	if !strings.Contains(frame, "still working") || !strings.Contains(frame, "60-90s") {
		t.Errorf("a slow first step with no prompt pending lost its hint:\n%s", frame)
	}
}
