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
