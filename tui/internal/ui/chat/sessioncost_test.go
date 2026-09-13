// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"math"
	"strings"
	"testing"
	"time"
)

func ledger() sessionCost {
	var s sessionCost
	s.add(turnCost{duration: 20 * time.Second, steps: 4, tools: 7,
		inTok: 8000, outTok: 400, cachedTok: 2000, measured: true})
	s.add(turnCost{duration: 40 * time.Second, steps: 9, tools: 12,
		inTok: 12000, outTok: 600, cachedTok: 6000, measured: true})
	return s
}

func TestSessionCostSumsEveryMetric(t *testing.T) {
	d, steps, tools, in, out, cached, measured := ledger().totals()
	for _, c := range []struct {
		name string
		got  int
		want int
	}{
		{"steps", steps, 13}, {"tools", tools, 19},
		{"input", in, 20000}, {"output", out, 1000},
		{"cached", cached, 8000}, {"measured turns", measured, 2},
	} {
		if c.got != c.want {
			t.Errorf("%s = %d, want %d", c.name, c.got, c.want)
		}
	}
	if d != time.Minute {
		t.Errorf("wall time = %v, want 1m", d)
	}
}

// A turn the backend did not measure still happened. Counting it as a turn but
// not in the token totals is what lets the view say how much it actually covers.
func TestAnUnmeasuredTurnCountsAsATurnNotAsZeroTokens(t *testing.T) {
	s := ledger()
	s.add(turnCost{duration: 5 * time.Second, steps: 2, tools: 1})

	_, _, _, in, _, _, measured := s.totals()
	if in != 20000 {
		t.Errorf("input = %d, want the two measured turns' 20000", in)
	}
	if measured != 2 || len(s.turns) != 3 {
		t.Errorf("measured=%d of %d turns, want 2 of 3", measured, len(s.turns))
	}

	out := s.render(100, "some-model", nil)
	if !strings.Contains(out, "1 of 3 turns reported no token counts") {
		t.Errorf("the view hides that a turn went unmeasured:\n%s", out)
	}
}

// Without a rate card the view shows tokens and says so. An invented rate in a
// cost readout looks like a measurement and nobody re-checks it.
func TestNoPriceMeansNoDollars(t *testing.T) {
	out := ledger().render(100, "fireworks.glm-5p2", nil)
	if strings.Contains(out, "$") {
		t.Errorf("a dollar figure appeared with no price configured:\n%s", out)
	}
	if !strings.Contains(out, "no price configured") {
		t.Errorf("the view does not say why there is no cost:\n%s", out)
	}
	for _, want := range []string{"20.0k", "1.0k", "13", "19"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in:\n%s", want, out)
		}
	}
}

func TestCachedInputIsBilledAtItsOwnRate(t *testing.T) {
	cachedRate := 0.02
	p := &modelPrice{InputPerMTok: 0.20, OutputPerMTok: 0.80, CachedPerMTok: &cachedRate}

	// 12,000 uncached @ .20 + 8,000 cached @ .02 + 1,000 out @ .80
	want := 12000.0/1e6*0.20 + 8000.0/1e6*0.02 + 1000.0/1e6*0.80
	if got := p.totalUSD(20000, 8000, 1000); math.Abs(got-want) > 1e-12 {
		t.Errorf("totalUSD = %.8f, want %.8f", got, want)
	}

	// Absent cached rate means cached bills as input — a different offer from
	// "cached is free", so the two must not collapse together.
	q := &modelPrice{InputPerMTok: 0.20, OutputPerMTok: 0.80}
	got, want := q.totalUSD(20000, 8000, 1000), 20000.0/1e6*0.20+1000.0/1e6*0.80
	if math.Abs(got-want) > 1e-12 {
		t.Errorf("absent cached rate = %.8f, want input rate %.8f", got, want)
	}
}

func TestEmptySessionSaysSoRatherThanShowingZeroes(t *testing.T) {
	var s sessionCost
	if out := s.render(80, "m", nil); !strings.Contains(out, "No turns yet") {
		t.Errorf("empty ledger rendered as data:\n%s", out)
	}
}
