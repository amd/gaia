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

// The whole point of ranking: the narrower the window, the more the user needs
// the way out and the less they need to be told the wheel scrolls. A plain
// right-hand truncation does the exact opposite.
func TestANarrowBarKeepsTheWayOutAndDropsTheNiceties(t *testing.T) {
	hints := []hint{
		{text: "step 12", rank: rankDiagnostic},
		{text: "↑↓ scroll", rank: rankAffordance},
		{text: "Esc cancel", rank: rankInterrupt},
		{text: "Ctrl+C quit", rank: rankEscape},
	}

	for _, width := range []int{11, 15, 24, 40} {
		got := fitHints(hints, width)
		if !strings.Contains(got, "Ctrl+C quit") {
			t.Errorf("width %d dropped the escape hatch: %q", width, got)
		}
		if w := ansi.StringWidth(got); w > width {
			t.Errorf("width %d overflowed to %d columns: %q", width, w, got)
		}
		if strings.Contains(got, "…") {
			t.Errorf("width %d truncated an item instead of dropping one: %q", width, got)
		}
	}

	// Tight: only the escape hatch survives, and the diagnostic goes first.
	tight := fitHints(hints, 12)
	if tight != "Ctrl+C quit" {
		t.Errorf("at 12 columns the bar reads %q; want just the way out", tight)
	}

	// Roomy: everything shows, in the order it was appended.
	full := fitHints(hints, 80)
	if full != "step 12 · ↑↓ scroll · Esc cancel · Ctrl+C quit" {
		t.Errorf("a wide bar changed the order or lost an item: %q", full)
	}
}

// Ranks decide the order of loss, not the order of display.
func TestItemsAreDroppedByRankNotByPosition(t *testing.T) {
	hints := []hint{
		{text: "step 3", rank: rankDiagnostic},
		{text: "End to jump to latest", rank: rankOrient},
		{text: "Ctrl+C quit", rank: rankEscape},
	}
	got := fitHints(hints, 36)
	if strings.Contains(got, "step 3") {
		t.Errorf("the diagnostic outlived the orientation hint: %q", got)
	}
	if !strings.Contains(got, "End to jump to latest") {
		t.Errorf("the way back to the newest content was dropped too early: %q", got)
	}
}

// A terminal too narrow for even one item gets that item whole, clipped by the
// bar. Returning an empty string would be worse — the bar would say nothing.
func TestAnImpossiblyNarrowBarStillNamesTheWayOut(t *testing.T) {
	got := fitHints([]hint{
		{text: "↑↓ scroll", rank: rankAffordance},
		{text: "Ctrl+C quit", rank: rankEscape},
	}, 3)
	if got != "Ctrl+C quit" {
		t.Errorf("got %q; want the escape hatch alone", got)
	}
}

// The measure has to be display columns: "↑↓ scroll · Ctrl+C quit" is 23
// columns but 27 bytes, and a byte count drops an item that would have fit.
func TestHintsAreMeasuredInColumnsNotBytes(t *testing.T) {
	hints := []hint{
		{text: "↑↓ scroll", rank: rankAffordance},
		{text: "Ctrl+C quit", rank: rankEscape},
	}
	full := "↑↓ scroll · Ctrl+C quit"
	if got := fitHints(hints, ansi.StringWidth(full)); got != full {
		t.Errorf("both items fit in %d columns but got %q", ansi.StringWidth(full), got)
	}
	if len(full) <= ansi.StringWidth(full) {
		t.Fatal("test premise broken: the string is no longer multi-byte")
	}
}

// The step counter is machinery, not progress — it stays out of a normal
// session entirely.
func TestTheStepCounterIsDevOnly(t *testing.T) {
	m := newTestChat(t)
	m.totalSteps = 7

	for _, h := range m.statusHints() {
		if strings.HasPrefix(h.text, "step ") {
			t.Errorf("the agent loop's step count leaked into a normal session: %q", h.text)
		}
	}

	m.dev = true
	var found bool
	for _, h := range m.statusHints() {
		if h.text == "step 7" {
			found = true
		}
	}
	if !found {
		t.Error("--dev lost the step counter")
	}
}

// Each state advertises what actually applies in it.
func TestHintsMatchTheState(t *testing.T) {
	busy := newTestChat(t)
	if got := joinHints(busy.statusHints()); !strings.Contains(got, "keep typing") ||
		!strings.Contains(got, "Esc cancel") {
		t.Errorf("a running turn does not advertise type-ahead or cancel: %q", got)
	}

	idle := newTestChat(t)
	idle.streaming = false
	if got := joinHints(idle.statusHints()); strings.Contains(got, "Esc cancel") {
		t.Errorf("an idle session offers to cancel nothing: %q", got)
	}

	scrolled := newTestChat(t)
	scrolled.followTail = false
	if got := joinHints(scrolled.statusHints()); !strings.Contains(got, "End to jump to latest") {
		t.Errorf("a scrolled-away reader is not told the way back: %q", got)
	}
}

// The startup restore says what the wait is, not that typing helps; once it
// ends the ordinary turn hints come back.
func TestTheStartupRestoreSaysWhatItIsRestoring(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	got := joinHints(m.statusHints())
	if !strings.Contains(got, "restoring "+flashID+"…") {
		t.Fatalf("the restore turn does not say what it is restoring: %q", got)
	}
	if strings.Contains(got, "keep typing") {
		t.Fatalf("the restore turn still advertises typing ahead: %q", got)
	}
	if !m.streaming {
		t.Fatalf("the restore turn is not streaming, so this test proves nothing about Esc")
	}
	if !strings.Contains(got, "Esc cancel") {
		t.Fatalf("Esc still cancels the restore, but the bar no longer says so: %q", got)
	}

	m = deliver(t, m,
		ping(flashID, flashID, "fireworks", true),
		ping(flashID, flashID, "fireworks", true),
		event.CanonicalFinalEvent{Type: "final", Answer: "Switched to **" + flashID + "**."},
	)
	if got := joinHints(m.statusHints()); strings.Contains(got, "restoring") {
		t.Fatalf("the restore hint outlived the restore: %q", got)
	}
	m.streaming = true
	if got := joinHints(m.statusHints()); !strings.Contains(got, "keep typing") ||
		!strings.Contains(got, "Esc cancel") {
		t.Fatalf("a normal turn after the restore lost its hints: %q", got)
	}
}

// A relayed restore has no stream of its own, so Esc has nothing to cancel and
// the bar must not offer it.
func TestARelayedRestoreDoesNotOfferEscCancel(t *testing.T) {
	m, c, _ := startupChat(t, StartupRestore, "fireworks", flashID)
	m = openChat(t, m, c)
	m.streaming = false
	got := joinHints(m.statusHints())
	if !strings.Contains(got, "restoring "+flashID+"…") {
		t.Fatalf("the relayed restore does not say what it is restoring: %q", got)
	}
	if strings.Contains(got, "Esc cancel") {
		t.Fatalf("a restore with no stream offers a cancel Esc cannot do: %q", got)
	}
}

// A model picked at the gate is not a restore, and an empty saved model arms
// no opening turn at all, so neither claims to be restoring anything.
func TestOnlyARestoreSaysRestoring(t *testing.T) {
	picked, c, _ := startupChat(t, StartupPicked, "fireworks", flashID)
	picked = openChat(t, picked, c)
	if got := joinHints(picked.statusHints()); strings.Contains(got, "restoring") {
		t.Fatalf("a gate pick claims to be a restore: %q", got)
	}

	empty, _, _ := startupChat(t, StartupRestore, "local", "   ")
	empty.streaming = true
	if got := joinHints(empty.statusHints()); strings.Contains(got, "restoring") {
		t.Fatalf("an empty saved model produced a restore hint: %q", got)
	}
}

// A model id too long for the bar is dropped whole, never cut mid-name, and
// never at the cost of the way out.
func TestALongRestoreNameNeverCrowdsOutTheWayOut(t *testing.T) {
	long := "user." + strings.Repeat("Very-Long-Model-Name-", 6) + "GGUF"
	m, c, _ := startupChat(t, StartupRestore, "local", long)
	m = openChat(t, m, c)
	got := joinHints(m.statusHints())
	if !strings.Contains(got, "restoring "+long+"…") {
		t.Fatalf("the full restore hint is missing before fitting: %q", got)
	}
	fitted := fitHints(m.statusHints(), 40)
	if !strings.Contains(fitted, "Ctrl+C quit") {
		t.Fatalf("a long model name crowded out the way out: %q", fitted)
	}
	if strings.Contains(fitted, "restoring") && !strings.Contains(fitted, long+"…") {
		t.Fatalf("the model name was cut mid-word: %q", fitted)
	}
}

func joinHints(hints []hint) string {
	var out []string
	for _, h := range hints {
		out = append(out, h.text)
	}
	return strings.Join(out, hintSeparator)
}

// Claude is the most obviously metered thing the TUI runs: the user is paying
// Anthropic per token. Keying the spend line on a "fireworks." prefix left
// exactly that case with no running total.
func TestClaudeSessionsShowTheirSpend(t *testing.T) {
	spent := sessionCost{}
	spent.add(turnCost{duration: time.Second, steps: 1, tools: 0,
		inTok: 50000, outTok: 2000, cachedTok: 10000, measured: true})

	for _, tc := range []struct {
		name    string
		model   ChatModel
		metered bool
	}{
		{"claude", ChatModel{modelBackend: "claude", modelID: "claude-sonnet-5", modelRemote: true}, true},
		{"fireworks", ChatModel{modelBackend: "fireworks", modelID: "fireworks.glm-5p3", modelRemote: true}, true},
		{"amd gateway", ChatModel{modelBackend: "amd", modelID: "amd.gpt-4.1", modelRemote: true}, false},
		{"local", ChatModel{modelBackend: "lemonade", modelID: "Gemma-4-E4B-it-GGUF"}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := tc.model
			m.cost = spent
			if got := m.isMeteredModel(); got != tc.metered {
				t.Fatalf("isMeteredModel = %v, want %v", got, tc.metered)
			}
			hint := m.sessionSpendHint()
			if tc.metered && hint == "" {
				t.Error("a metered session shows no running spend")
			}
			if !tc.metered && hint != "" {
				t.Errorf("an unmetered session claims a spend: %q", hint)
			}
		})
	}
}

// A session that really has spent something must never render as "$0.000" —
// that reads as free, and /cost would disagree with the status bar.
func TestATinySpendIsNotShownAsZero(t *testing.T) {
	noPriceFile(t)
	m := ChatModel{modelBackend: "fireworks", modelID: "fireworks.glm-5p3-flash", modelRemote: true}
	m.cost.add(turnCost{duration: time.Second, steps: 1, inTok: 100, outTok: 10, measured: true})

	hint := m.sessionSpendHint()
	if strings.Contains(hint, "$0.000 ") {
		t.Errorf("a real spend rendered as zero: %q", hint)
	}
	if !strings.Contains(hint, "<$0.001") {
		t.Errorf("want a below-threshold marker, got %q", hint)
	}
}
