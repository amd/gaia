// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Turning measured tokens into money.
//
// Two rules decide what may appear here. A rate is either the provider's own
// published number or the user's own; nothing is ever estimated, interpolated
// from a neighbouring model, or carried over from a family member. And a model
// with no rate shows tokens and no dollars — an absent figure sends the reader
// to look it up, where a guessed one just quietly prices their month wrong.
//
// Staleness is handled by disclosure rather than by refusing to have an
// opinion: every dollar figure is rendered with the date its rate was read.
//
// A user file overrides the built-ins for any model, read fresh each time:
//
//	~/.gaia/model-prices.json
//	{
//	  "fireworks.glm-5p3": {
//	    "input_per_mtok": 1.40, "output_per_mtok": 4.40, "cached_per_mtok": 0.26
//	  }
//	}
//
// Matching is exact, then longest-prefix, so one entry can cover a family.

// pricesAsOf is the day the built-in rates below were read from the provider's
// published table. Rendered next to every dollar figure, because the one thing
// a cost readout must never do is look current when it is not.
const pricesAsOf = "2026-09-13"

// pricesSource is where the built-in rates came from, so the next person can
// check them without guessing which page.
const pricesSource = "https://docs.fireworks.ai/serverless/pricing"

// builtinPrices are the provider's published serverless rates, in dollars per
// million tokens, read on the date above.
//
// Shipping them is the difference between a feature that costs your work and
// one that asks you to go and look the numbers up first. The staleness risk is
// handled by showing the date rather than by refusing to have an opinion — and
// a model that is NOT in this table still shows tokens only, never a guessed
// rate, because a wrong number is worse than an absent one.
//
// Keys match on longest prefix (see lookupPrice), so the routers under a family
// inherit the family's rate.
var builtinPrices = map[string]modelPrice{
	// GLM 5.2 Fast — standard serverless tier.
	"fireworks.accounts/fireworks/routers/glm-5p2-fast": {
		InputPerMTok: 2.10, OutputPerMTok: 6.60,
		CachedPerMTok: floatPtr(0.21), Currency: "USD",
	},
	// GLM 5.2 — standard serverless tier.
	"fireworks.glm-5p2": {
		InputPerMTok: 1.40, OutputPerMTok: 4.40,
		CachedPerMTok: floatPtr(0.14), Currency: "USD",
	},
	// GLM 5.3 Fast — standard serverless tier.
	"fireworks.accounts/fireworks/routers/glm-5p3-fast": {
		InputPerMTok: 2.10, OutputPerMTok: 6.60,
		CachedPerMTok: floatPtr(0.39), Currency: "USD",
	},
	// GLM 5.3 — standard serverless tier. Cached input is priced well above
	// 5.2's, so the family rate is NOT reusable between the two generations.
	"fireworks.glm-5p3": {
		InputPerMTok: 1.40, OutputPerMTok: 4.40,
		CachedPerMTok: floatPtr(0.26), Currency: "USD",
	},
	// GLM 5.3 Flash — listed after the entry it extends; the longer key wins
	// the prefix match, so Flash never inherits the full 5.3 rate.
	"fireworks.glm-5p3-flash": {
		InputPerMTok: 0.15, OutputPerMTok: 0.50,
		CachedPerMTok: floatPtr(0.03), Currency: "USD",
	},
}

func floatPtr(f float64) *float64 { return &f }

// modelPrice is a rate card in dollars per million tokens.
type modelPrice struct {
	InputPerMTok  float64 `json:"input_per_mtok"`
	OutputPerMTok float64 `json:"output_per_mtok"`
	// CachedPerMTok is charged for the cached part of the prompt. Zero means
	// cached input is free, which is a real offer some providers make — absent
	// means "same as input", so the two cases are written differently.
	CachedPerMTok *float64 `json:"cached_per_mtok,omitempty"`
	Currency      string   `json:"currency,omitempty"`

	// fromUser marks a rate that came from the user's own file rather than the
	// built-in table, so the view can say which it quoted.
	fromUser bool
}

// source names where this rate came from, for the line under the cost.
func (p modelPrice) source() string {
	if p.fromUser {
		return "your model-prices.json"
	}
	return "published rates as of " + pricesAsOf
}

// totalUSD is the bill as a number, for callers that render it themselves.
func (p modelPrice) totalUSD(in, cached, out int) float64 {
	uncachedUSD, cachedUSD, outUSD := p.split(in, cached, out)
	return uncachedUSD + cachedUSD + outUSD
}

// split is one bill broken into the three things that are charged for.
// Written once: the clamp and the cached-rate rule are billing policy, and a
// second copy is a second place for them to drift.
func (p modelPrice) split(in, cached, out int) (uncachedUSD, cachedUSD, outUSD float64) {
	uncached := in - cached
	if uncached < 0 {
		uncached = 0
	}
	cachedRate := p.InputPerMTok
	if p.CachedPerMTok != nil {
		cachedRate = *p.CachedPerMTok
	}
	return perMTok(uncached, p.InputPerMTok),
		perMTok(cached, cachedRate),
		perMTok(out, p.OutputPerMTok)
}

// format renders the cost of one token bill.
func (p modelPrice) format(in, cached, out int) string {
	cur := p.Currency
	if cur == "" {
		cur = "USD"
	}
	uncachedUSD, cachedUSD, outUSD := p.split(in, cached, out)
	parts := []string{fmt.Sprintf("$%.4f %s", p.totalUSD(in, cached, out), cur)}
	if cached > 0 {
		parts = append(parts, fmt.Sprintf("(input $%.4f + cached $%.4f + output $%.4f)",
			uncachedUSD, cachedUSD, outUSD))
	}
	return strings.Join(parts, "  ")
}

// costHelp explains where rates come from and how to set your own.
//
// Reachable as "/cost help" because the readout points there — a readout that
// names a command which does not exist sends the question to the agent as a
// chat message, which is worse than saying nothing.
func costHelp(model string) string {
	var b strings.Builder
	b.WriteString("Where cost figures come from\n\n")
	b.WriteString("  Token counts are measured — every figure is summed from what the\n")
	b.WriteString("  backend reported for each turn. Nothing is estimated.\n\n")
	fmt.Fprintf(&b, "  Rates are the provider's published serverless prices, read on %s:\n", pricesAsOf)
	fmt.Fprintf(&b, "  %s\n\n", pricesSource)
	b.WriteString("  A model with no published rate here shows tokens and no dollars,\n")
	b.WriteString("  rather than a guess.\n\n")
	b.WriteString("To set your own rate — a negotiated price, a tier not listed, or a\n")
	b.WriteString("correction after the provider moves its prices:\n\n")
	fmt.Fprintf(&b, "  %s\n", priceFilePath())
	b.WriteString("  {\n")
	if model != "" {
		fmt.Fprintf(&b, "    %q: {\n", model)
	} else {
		b.WriteString("    \"fireworks.glm-5p3\": {\n")
	}
	b.WriteString("      \"input_per_mtok\": 1.40,\n")
	b.WriteString("      \"output_per_mtok\": 4.40,\n")
	b.WriteString("      \"cached_per_mtok\": 0.26\n")
	b.WriteString("    }\n  }\n\n")
	b.WriteString("  Dollars per million tokens. Your file wins over the published rates.\n")
	b.WriteString("  Keys match exactly first, then by longest prefix, so one entry can\n")
	b.WriteString("  cover a family. Omitting cached_per_mtok bills cached input at the\n")
	b.WriteString("  full input rate; setting it to 0 means cached input is free — those\n")
	b.WriteString("  are different offers, so they are written differently.\n\n")
	b.WriteString("  GAIA_MODEL_PRICES overrides the path above.\n")
	return b.String()
}

func perMTok(tokens int, ratePerMTok float64) float64 {
	return float64(tokens) / 1_000_000 * ratePerMTok
}

// priceFilePath is where the rate card lives.
func priceFilePath() string {
	if p := strings.TrimSpace(os.Getenv("GAIA_MODEL_PRICES")); p != "" {
		return p
	}
	home := strings.TrimSpace(os.Getenv("GAIA_HOME"))
	if home == "" {
		h, err := os.UserHomeDir()
		if err != nil {
			return ""
		}
		home = filepath.Join(h, ".gaia")
	}
	return filepath.Join(home, "model-prices.json")
}

// lookupPrice returns the rate card for a model, or nil when none is known.
//
// The user's file wins over the built-in table: a negotiated rate, a tier this
// table does not model, or a correction after the provider moves its prices all
// have to be expressible without waiting for a release. Falling back to the
// built-ins means the common case needs no configuration at all.
//
// Read on every call rather than cached: editing the file is how a user
// corrects a rate, and a readout that keeps quoting the old one until restart
// is the same staleness problem by another route.
func lookupPrice(model string) *modelPrice {
	table, _ := userPriceTable()
	if p := lookupIn(table, model); p != nil {
		p.fromUser = true
		return p
	}
	return lookupIn(builtinPrices, model)
}

// userPriceTable is ~/.gaia/model-prices.json.
//
// The error is non-nil only when a file is there and could not be used. No
// file at all is the ordinary case and not a problem — see priceFileProblem
// for why the difference has to reach the user.
func userPriceTable() (map[string]modelPrice, error) {
	path := priceFilePath()
	if path == "" {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("reading %s: %w", path, err)
	}
	var table map[string]modelPrice
	if err := json.Unmarshal(raw, &table); err != nil {
		return nil, fmt.Errorf("parsing %s: %w", path, err)
	}
	return table, nil
}

// priceFileProblem is the reason the user's rate card was ignored, or nil.
//
// A typo in that file used to present as "no price configured" — so the user
// went and edited a file that was already being discarded, with nothing
// anywhere to tell them why. A rate the user set and a rate that failed to
// load are different states and the readout has to say which.
func priceFileProblem() error {
	_, err := userPriceTable()
	return err
}

// lookupIn resolves a model against one table: exact match, then longest prefix.
func lookupIn(table map[string]modelPrice, model string) *modelPrice {
	if len(table) == 0 {
		return nil
	}
	if p, ok := table[model]; ok {
		return &p
	}
	// Longest prefix, so "fireworks." can price a whole family without listing
	// every router under it.
	best, bestLen := (*modelPrice)(nil), -1
	for key, p := range table {
		if strings.HasPrefix(model, key) && len(key) > bestLen {
			entry := p
			best, bestLen = &entry, len(key)
		}
	}
	return best
}
