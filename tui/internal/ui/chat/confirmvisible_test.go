// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/components"
)

// The confirmation must be ON THE SCREEN, not merely on the model.
//
// This file exists because the old tests asserted `m.confirmation != nil` and
// that View() contained the prompt on a three-line transcript — both true,
// both passing, while a real session showed the user nothing. The modal was
// written into the viewport's CONTENT, so a transcript longer than the
// viewport pushed it below the fold; and a pending modal owns the keyboard, so
// `end`, PgUp and the arrows could not scroll to it. The measured result was
// 442s of `● GAIA streaming` with no visible question, ended only by Esc.
//
// The rule these tests encode: whatever is above it, the prompt is in the
// frame. Assert on the rendered frame, never on the model field.

// transcript fills the model with enough turns to overflow any viewport.
//
// It resizes first: a real TUI always gets a WindowSizeMsg before its first
// frame, and liveModel only assigns width/height. Without it the first resize
// happens inside the event under test and its effect is mistaken for the
// modal's.
func transcript(m ChatModel, turns int) ChatModel {
	m.resize()
	for i := 0; i < turns; i++ {
		m.messages = append(m.messages,
			Message{Role: RoleUser, Content: fmt.Sprintf("question number %d", i)},
			Message{Role: RoleAssistant, Content: strings.Repeat(
				fmt.Sprintf("answer %d filler text that wraps and takes rows. ", i), 6)},
		)
	}
	m.updateViewport()
	return m
}

// visibleFrame is what the terminal actually paints — View() clipped to the
// model's height, exactly as a screen capture would see it.
func visibleFrame(m ChatModel) string {
	lines := strings.Split(m.View(), "\n")
	if len(lines) > m.height {
		lines = lines[:m.height]
	}
	return strings.Join(lines, "\n")
}

func TestTheConfirmationIsVisibleUnderALongTranscript(t *testing.T) {
	for _, turns := range []int{0, 3, 40} {
		t.Run(fmt.Sprintf("%d_prior_turns", turns), func(t *testing.T) {
			m, _ := liveModel(t)
			m.streaming = true
			m = transcript(m, turns)
			m = feed(t, m, gatedShellCall())

			frame := visibleFrame(m)
			for _, want := range []string{
				`command="pwd"`, // the command, verbatim
				"y run once",    // and that it can be answered
				"n/esc deny",
			} {
				if !strings.Contains(frame, want) {
					t.Errorf("the prompt is off-screen with %d prior turns (missing %q):\n%s",
						turns, want, frame)
				}
			}
		})
	}
}

// The pinned block must take its rows from the transcript, not from the frame:
// pushing the composer or the status bar off the bottom trades one invisible
// thing for another.
func TestPinningTheConfirmationDoesNotPushOffTheComposer(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = transcript(m, 40)

	before := strings.Count(m.View(), "\n")
	m = feed(t, m, gatedShellCall())
	after := strings.Count(m.View(), "\n")

	if after != before {
		t.Errorf("the frame grew from %d rows to %d when the prompt went up; "+
			"it must fit inside the terminal, not extend past it", before+1, after+1)
	}
	frame := visibleFrame(m)
	if !strings.Contains(frame, "waiting for your answer") {
		t.Errorf("the status bar was pushed out of the frame:\n%s", frame)
	}
}

// While a decision is pending the turn is not producing anything. Saying
// "streaming" for minutes is what makes an unanswered question read as a hang.
func TestTheStatusBarSaysADecisionIsPending(t *testing.T) {
	m, _ := liveModel(t)
	m.resize()
	m.streaming = true

	if strings.Contains(m.View(), "waiting for your answer") {
		t.Fatal("a plain streaming turn must not claim to be waiting on the user")
	}

	m = feed(t, m, gatedShellCall())
	frame := visibleFrame(m)
	if !strings.Contains(frame, "waiting for your answer") {
		t.Errorf("the status bar still says the turn is streaming:\n%s", frame)
	}
	if strings.Contains(frame, "gaia streaming") {
		t.Errorf("both states are on screen at once:\n%s", frame)
	}
	// The modal says "n/esc deny". A status hint saying "Esc cancel" one row
	// below it gives the same key two meanings on one screen.
	if strings.Contains(frame, "Esc cancel") {
		t.Errorf("the status hint contradicts the modal about Esc:\n%s", frame)
	}
}

// Answering it puts the rows back.
func TestResolvingTheConfirmationReturnsTheRowsToTheTranscript(t *testing.T) {
	m, _ := liveModel(t)
	m.streaming = true
	m = transcript(m, 40)
	tall := m.viewport.Height

	m = feed(t, m, gatedShellCall())
	if m.viewport.Height >= tall {
		t.Fatalf("the transcript did not give up rows for the prompt: %d -> %d",
			tall, m.viewport.Height)
	}

	updated, cmd := m.handleKey(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("n")})
	m = updated.(ChatModel)
	updated2, _ := m.Update(cmd().(components.ConfirmationDecidedMsg))
	m = updated2.(ChatModel)

	if m.confirmation != nil {
		t.Fatal("a resolved confirmation must clear")
	}
	if m.viewport.Height != tall {
		t.Errorf("the transcript did not get its rows back: %d, want %d",
			m.viewport.Height, tall)
	}
	if strings.Contains(visibleFrame(m), "y run once") {
		t.Error("the answered prompt is still on screen")
	}
}

// A terminal too short for both is the case where clipping the modal is the
// lesser evil — but it must still render, and the frame must still fit.
func TestAVeryShortTerminalStillShowsTheCommand(t *testing.T) {
	m, _ := liveModel(t)
	m.width, m.height = 100, 12
	m.resize()
	m.streaming = true
	m = transcript(m, 20)
	m = feed(t, m, gatedShellCall())

	frame := visibleFrame(m)
	if !strings.Contains(frame, `command="pwd"`) {
		t.Errorf("the command is not on a 12-row screen:\n%s", frame)
	}
	if rows := strings.Count(m.View(), "\n") + 1; rows > m.height {
		t.Errorf("the frame is %d rows on a %d-row terminal", rows, m.height)
	}
}
