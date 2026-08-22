// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"
)

// The dev footer published "219 tokens · 2142.6 tok/s" for a local Gemma-4-E4B
// turn — about twenty times what the hardware can do. The turn had not streamed:
// ttft was 46.2s of a 46.3s turn, so the rate was computed from the 0.1s sliver
// left over, which measures frame scheduling rather than generation.

func TestTheRateIsWithheldWhenNothingStreamed(t *testing.T) {
	// The observed case.
	if rate, ok := tokensPerSecond(219, 46300*time.Millisecond, 46200*time.Millisecond); ok {
		t.Errorf("published %.1f tok/s from a 0.1s window; it should be withheld", rate)
	}
}

func TestARealGenerationWindowIsReported(t *testing.T) {
	rate, ok := tokensPerSecond(200, 12*time.Second, 2*time.Second)
	if !ok {
		t.Fatal("a 10s generation window is measurable and should be reported")
	}
	if want := 20.0; rate != want {
		t.Errorf("rate = %.2f, want %.2f", rate, want)
	}
}

func TestEdges(t *testing.T) {
	cases := []struct {
		name           string
		tokens         int
		duration, ttft time.Duration
		wantOK         bool
	}{
		{"no token count", 0, 10 * time.Second, time.Second, false},
		{"negative window", 100, time.Second, 5 * time.Second, false},
		{"zero window", 100, 5 * time.Second, 5 * time.Second, false},
		{"just under the floor", 100, 1900 * time.Millisecond, time.Second, false},
		{"exactly at the floor", 100, 2 * time.Second, time.Second, true},
		{"well over the floor", 100, 30 * time.Second, time.Second, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, ok := tokensPerSecond(tc.tokens, tc.duration, tc.ttft); ok != tc.wantOK {
				t.Errorf("ok = %v, want %v", ok, tc.wantOK)
			}
		})
	}
}

// The token COUNT is a real measurement and must survive even when the rate
// cannot be derived from it — losing both would hide that the turn was measured.
func TestTheTokenCountSurvivesAWithheldRate(t *testing.T) {
	m := ChatModel{dev: true}
	msg := &Message{
		Duration: 46300 * time.Millisecond,
		TTFT:     46200 * time.Millisecond,
		Tokens:   219,
		Steps:    2,
	}

	stats := m.answerStats(msg)
	if !strings.Contains(stats, "219 tokens") {
		t.Errorf("the token count was dropped along with the rate: %q", stats)
	}
	if strings.Contains(stats, "tok/s") {
		t.Errorf("an unmeasurable rate was printed anyway: %q", stats)
	}
}

func TestTheRateAppearsWhenItWasMeasured(t *testing.T) {
	m := ChatModel{dev: true}
	stats := m.answerStats(&Message{
		Duration: 12 * time.Second,
		TTFT:     2 * time.Second,
		Tokens:   200,
	})
	if !strings.Contains(stats, "tok/s") {
		t.Errorf("a measurable rate is missing from the footer: %q", stats)
	}
}

// Non-dev mode shows one number, and that contract is unchanged.
func TestNonDevStillShowsOnlyTheDuration(t *testing.T) {
	stats := ChatModel{}.answerStats(&Message{
		Duration: 12 * time.Second,
		TTFT:     2 * time.Second,
		Tokens:   200,
	})
	if strings.Contains(stats, "tok/s") || strings.Contains(stats, "tokens") {
		t.Errorf("harness telemetry leaked outside --dev: %q", stats)
	}
}

// Two real turns from a local Gemma-4-E4B session, one of each kind. The
// absolute floor alone passed the second one and published a rate about
// thirteen times the hardware's real speed.
func TestRealLocalTurns(t *testing.T) {
	t.Run("streamed — reported", func(t *testing.T) {
		// 74.6s turn, first token at 43.8s: 41% of the turn spent generating.
		rate, ok := tokensPerSecond(1366, 74600*time.Millisecond, 43800*time.Millisecond)
		if !ok {
			t.Fatal("a genuinely streamed turn was withheld")
		}
		if rate < 30 || rate > 60 {
			t.Errorf("rate %.1f tok/s is not the ~44 this turn actually ran at", rate)
		}
	})

	t.Run("single frame — withheld", func(t *testing.T) {
		// 24.8s turn, first token at 23.6s: 5% of the turn. Nothing streamed.
		if rate, ok := tokensPerSecond(700, 24800*time.Millisecond, 23600*time.Millisecond); ok {
			t.Errorf("published %.1f tok/s from a 1.2s sliver of a 24.8s turn", rate)
		}
	})
}

// A short turn that really did stream keeps its rate: the share test must not
// throw away fast answers, only artifacts.
func TestAFastStreamedTurnKeepsItsRate(t *testing.T) {
	if _, ok := tokensPerSecond(120, 4*time.Second, 500*time.Millisecond); !ok {
		t.Error("a 4s turn that streamed for 3.5s was withheld")
	}
}
