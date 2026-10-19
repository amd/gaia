// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

const restoredID = "Qwen3.6-35B-A3B-GGUF"

func confirmRestore(t *testing.T, m ChatModel) ChatModel {
	t.Helper()
	return deliver(t, m,
		ping(restoredID, restoredID, "lemonade", false),
		event.CanonicalFinalEvent{Type: "final", Answer: "Switched to " + restoredID + ". Chat runs locally."},
	)
}

func TestHeaderNamesTheRestoringModelNotTheGateDefault(t *testing.T) {
	m, _, _ := startupChat(t, StartupRestore, "local", restoredID)
	m = m.WithExpectedModel("Gemma-4-E4B-it-GGUF")
	header := ansi.Strip(m.renderHeader())
	if !strings.Contains(header, restoredID) || strings.Contains(header, "Gemma") {
		t.Fatalf("header must name the model being restored: %q", header)
	}
}

func TestRefusedRestoreHandsTheHeaderBackToTheGateModel(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "local", restoredID)
	m = m.WithExpectedModel("Gemma-4-E4B-it-GGUF")
	m = openChat(t, m, c)
	m = deliver(t, m, event.CanonicalErrorEvent{Type: "error", Detail: "Unknown Lemonade model: " + restoredID})
	header := ansi.Strip(m.renderHeader())
	if strings.Contains(header, restoredID) || !strings.Contains(header, "Gemma-4-E4B-it-GGUF") {
		t.Fatalf("a refused restore must not leave its target in the header: %q", header)
	}
}

func TestRestoreTurnReadsAsGettingReadyNotStreaming(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "local", restoredID)
	m = openChat(t, m, c)
	view := ansi.Strip(m.View())
	if strings.Contains(view, "streaming") || !strings.Contains(view, "getting ready") {
		t.Fatalf("the opening restore must not look like a user turn:\n%s", view)
	}
}

func TestRestoreIsStatedOnceWithoutAStrayTime(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "local", restoredID)
	m = confirmRestore(t, openChat(t, m, c))
	last := m.messages[len(m.messages)-1]
	if strings.Contains(last.Content, "Switched to") || !strings.HasPrefix(last.Content, "Restored your last model: "+restoredID) {
		t.Fatalf("restore should be said once: %q", last.Content)
	}
	if s := m.answerStats(&last); s != "" {
		t.Fatalf("the restore line printed a bare time: %q", s)
	}
}

// #5122: the opening model switch's time is really the agent starting up, so
// it is never printed as "model switched in N s" — there is no mode that
// labels it, and no recap line under the restore notice.
func TestTheRestoreNeverPrintsASwitchTime(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "local", restoredID)
	m = openChat(t, m, c)
	m.queryStart = m.queryStart.Add(-25 * time.Second)
	m = confirmRestore(t, m)
	last := m.messages[len(m.messages)-1]
	m.expandWork = true
	if s := m.answerStats(&last); s != "" {
		t.Fatalf("the restore printed a time: %q", s)
	}
	if out := ansi.Strip(m.View()); strings.Contains(out, "switched in") {
		t.Fatalf("the restore was labelled as a slow model switch:\n%s", out)
	}
}

func TestAnOrdinaryAnswerKeepsItsTimeUnderCtrlO(t *testing.T) {
	m := ChatModel{expandWork: true}
	msg := Message{Duration: 2 * time.Second}
	if s := m.answerStats(&msg); s != "2.0s" {
		t.Fatalf("the expanded stats lost the bare time: %q", s)
	}
	if s := m.recapLine(&msg); s != "2.0s" {
		t.Fatalf("a turn with no work recaps as its time alone: %q", s)
	}
}

func TestReadyTimeCountsTheRestoreTurn(t *testing.T) {
	m, c := warmChat(t)
	m = openChat(t, m, &c.queryCapturingClient)
	began := m.startup.began
	m = deliver(t, m,
		ping("Qwen3-4B-GGUF", "Qwen3-4B-GGUF", "lemonade", false),
		event.CanonicalFinalEvent{Type: "final", Answer: "Switched."},
	)
	updated, _ := m.Update(startWarmUpMsg{})
	m = updated.(ChatModel)
	if !m.warming || began.IsZero() || !m.warmStart.Equal(began) {
		t.Fatalf("warm-up clock must start at the restore turn: warmStart=%v began=%v", m.warmStart, began)
	}
}
