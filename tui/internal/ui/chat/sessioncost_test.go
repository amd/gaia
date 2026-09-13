// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// noPriceFile points the rate-card lookup at a path that does not exist, so a
// test asserts about the built-in table rather than about whatever the
// developer happens to have in ~/.gaia.
func noPriceFile(t *testing.T) {
	t.Helper()
	t.Setenv("GAIA_MODEL_PRICES", filepath.Join(t.TempDir(), "absent.json"))
}

// writePriceFile points the lookup at a file with these exact contents.
func writePriceFile(t *testing.T, contents string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "model-prices.json")
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatalf("writing price file: %v", err)
	}
	t.Setenv("GAIA_MODEL_PRICES", path)
	return path
}

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
		t.Errorf("active time = %v, want 1m", d)
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

	out := s.render("some-model", nil)
	if !strings.Contains(out, "1 of 3 turns reported no token counts") {
		t.Errorf("the view hides that a turn went unmeasured:\n%s", out)
	}
}

// Without a rate card the view shows tokens and says so. An invented rate in a
// cost readout looks like a measurement and nobody re-checks it.
func TestNoPriceMeansNoDollars(t *testing.T) {
	noPriceFile(t)
	out := ledger().render("a-model-nobody-has-priced", nil)
	if strings.Contains(out, "$") {
		t.Errorf("a dollar figure appeared with no price configured:\n%s", out)
	}
	if !strings.Contains(out, "no published rate") {
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
	if out := s.render("m", nil); !strings.Contains(out, "No turns yet") {
		t.Errorf("empty ledger rendered as data:\n%s", out)
	}
}

// The built-in table has to be real and reachable without configuration —
// that is the difference between a feature that costs your work and one that
// asks you to look the numbers up first.
func TestBuiltinRatesPriceTheModelsWeShip(t *testing.T) {
	noPriceFile(t)
	p := lookupPrice("fireworks.accounts/fireworks/routers/glm-5p2-fast")
	if p == nil {
		t.Fatal("no built-in rate for the model the TUI's own provider picker offers")
	}
	if p.InputPerMTok != 2.10 || p.OutputPerMTok != 6.60 || p.CachedPerMTok == nil || *p.CachedPerMTok != 0.21 {
		t.Errorf("built-in rate does not match the published card: %+v", p)
	}
	if strings.Contains(p.source(), "model-prices.json") {
		t.Error("a built-in rate claims it came from the user's file")
	}
}

// Every model the live endpoint serves under a GLM name must price exactly as
// the published card does. The two generations look interchangeable and are
// not: 5.3 charges nearly double 5.2 for cached input, which is the token
// class most of a long session is made of.
func TestGLMRatesMatchThePublishedCard(t *testing.T) {
	noPriceFile(t)
	for _, tc := range []struct {
		model           string
		in, cached, out float64
	}{
		{"fireworks.accounts/fireworks/routers/glm-5p2-fast", 2.10, 0.21, 6.60},
		{"fireworks.glm-5p2", 1.40, 0.14, 4.40},
		{"fireworks.accounts/fireworks/routers/glm-5p3-fast", 2.10, 0.39, 6.60},
		{"fireworks.glm-5p3", 1.40, 0.26, 4.40},
		{"fireworks.glm-5p3-flash", 0.15, 0.03, 0.50},
	} {
		p := lookupPrice(tc.model)
		if p == nil {
			t.Errorf("%s: no built-in rate", tc.model)
			continue
		}
		if p.CachedPerMTok == nil {
			t.Errorf("%s: no cached rate, so cached tokens bill at the full input rate", tc.model)
			continue
		}
		if p.InputPerMTok != tc.in || p.OutputPerMTok != tc.out || *p.CachedPerMTok != tc.cached {
			t.Errorf("%s: got %v/%v/%v, published card says %v/%v/%v",
				tc.model, p.InputPerMTok, *p.CachedPerMTok, p.OutputPerMTok, tc.in, tc.cached, tc.out)
		}
	}
}

// "fireworks.glm-5p3" is a prefix of "fireworks.glm-5p3-flash", and Flash is
// an order of magnitude cheaper. Whichever way the map iterates, the longer
// key has to win or every Flash session is billed as full 5.3.
func TestFlashIsNotPricedAsTheFullModel(t *testing.T) {
	noPriceFile(t)
	flash := lookupPrice("fireworks.glm-5p3-flash")
	full := lookupPrice("fireworks.glm-5p3")
	if flash == nil || full == nil {
		t.Fatal("missing a GLM 5.3 rate")
	}
	if flash.InputPerMTok >= full.InputPerMTok {
		t.Errorf("Flash priced at or above the full model: %v vs %v",
			flash.InputPerMTok, full.InputPerMTok)
	}
}

// A model nobody has priced shows tokens, never a guessed rate.
func TestAnUnknownModelHasNoPrice(t *testing.T) {
	noPriceFile(t)
	if p := lookupPrice("some-model-nobody-has-priced"); p != nil {
		t.Errorf("invented a rate for an unknown model: %+v", p)
	}
}


// A rate card that fails to load is a different state from never having set
// one, and the readout has to say which. Reported as "no price configured",
// the user edits a file that is already being discarded.
func TestABrokenPriceFileIsReportedNotSwallowed(t *testing.T) {
	writePriceFile(t, `{"fireworks.glm-5p3": {"input_per_mtok": 1.40,}}`)

	if err := priceFileProblem(); err == nil {
		t.Fatal("a malformed price file loaded without complaint")
	}
	out := ledger().render("fireworks.glm-5p3", nil)
	if !strings.Contains(out, "could not be read") {
		t.Errorf("the view does not say the price file failed:\n%s", out)
	}
	if strings.Contains(out, "no published rate") {
		t.Errorf("a broken file is reported as an unset one:\n%s", out)
	}
}

// No file at all is the ordinary case, not a failure.
func TestAnAbsentPriceFileIsNotAnError(t *testing.T) {
	noPriceFile(t)
	if err := priceFileProblem(); err != nil {
		t.Errorf("absent price file reported as a problem: %v", err)
	}
}

// The user's file wins, so a negotiated rate does not wait for a release.
func TestAUserRateOverridesTheBuiltinAndSaysSo(t *testing.T) {
	writePriceFile(t, `{"fireworks.glm-5p3": {"input_per_mtok": 0.5, "output_per_mtok": 1.0, "cached_per_mtok": 0.1}}`)
	p := lookupPrice("fireworks.glm-5p3")
	if p == nil {
		t.Fatal("user rate not found")
	}
	if p.InputPerMTok != 0.5 {
		t.Errorf("built-in rate won over the user's file: %+v", p)
	}
	if !strings.Contains(p.source(), "model-prices.json") {
		t.Errorf("a user rate does not say it came from their file: %q", p.source())
	}
}

// The readout points at /cost help, so /cost help has to exist and has to
// carry the thing the reader needs: where to write the file.
func TestCostHelpNamesThePriceFileAndTheRateSource(t *testing.T) {
	noPriceFile(t)
	help := costHelp("fireworks.glm-5p3")
	for _, want := range []string{priceFilePath(), pricesSource, "input_per_mtok", "cached_per_mtok", "GAIA_MODEL_PRICES"} {
		if !strings.Contains(help, want) {
			t.Errorf("/cost help does not mention %q:\n%s", want, help)
		}
	}
}

// The ledger sums the turns, so the label has to say active time. Calling it
// wall time made a half-hour session report one minute.
func TestTheLedgerCallsItActiveTimeNotWallTime(t *testing.T) {
	noPriceFile(t)
	out := ledger().render("a-model-nobody-has-priced", nil)
	if !strings.Contains(out, "active time") {
		t.Errorf("the duration is not labelled active time:\n%s", out)
	}
	if strings.Contains(out, "wall time") {
		t.Errorf("the sum of the turns is still called wall time:\n%s", out)
	}
}
