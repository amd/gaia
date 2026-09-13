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
// Prices are NOT compiled in. A provider's rate card changes without notice,
// and a stale constant in a cost readout is the one kind of wrong that nobody
// re-checks — it looks like a measurement. So a price is something the user
// states, and when they have not stated one the view says so and shows tokens
// only.
//
// The file is a plain JSON map, read fresh each time it is asked for:
//
//	~/.gaia/model-prices.json
//	{
//	  "fireworks.accounts/fireworks/routers/glm-5p2-fast": {
//	    "input_per_mtok": 0.22, "output_per_mtok": 0.88, "cached_per_mtok": 0.022
//	  }
//	}
//
// Matching is exact, then longest-prefix, so one entry can cover a family.

// modelPrice is a rate card in dollars per million tokens.
type modelPrice struct {
	InputPerMTok  float64 `json:"input_per_mtok"`
	OutputPerMTok float64 `json:"output_per_mtok"`
	// CachedPerMTok is charged for the cached part of the prompt. Zero means
	// cached input is free, which is a real offer some providers make — absent
	// means "same as input", so the two cases are written differently.
	CachedPerMTok *float64 `json:"cached_per_mtok,omitempty"`
	Currency      string   `json:"currency,omitempty"`
}

// totalUSD is the bill as a number, for callers that render it themselves.
func (p modelPrice) totalUSD(in, cached, out int) float64 {
	uncached := in - cached
	if uncached < 0 {
		uncached = 0
	}
	cachedRate := p.InputPerMTok
	if p.CachedPerMTok != nil {
		cachedRate = *p.CachedPerMTok
	}
	return perMTok(uncached, p.InputPerMTok) +
		perMTok(cached, cachedRate) +
		perMTok(out, p.OutputPerMTok)
}

// format renders the cost of one token bill.
func (p modelPrice) format(in, cached, out int) string {
	cur := p.Currency
	if cur == "" {
		cur = "USD"
	}
	uncached := in - cached
	if uncached < 0 {
		uncached = 0
	}
	cachedRate := p.InputPerMTok
	if p.CachedPerMTok != nil {
		cachedRate = *p.CachedPerMTok
	}
	total := perMTok(uncached, p.InputPerMTok) +
		perMTok(cached, cachedRate) +
		perMTok(out, p.OutputPerMTok)

	parts := []string{fmt.Sprintf("$%.4f %s", total, cur)}
	if cached > 0 {
		parts = append(parts, fmt.Sprintf("(input $%.4f + cached $%.4f + output $%.4f)",
			perMTok(uncached, p.InputPerMTok), perMTok(cached, cachedRate),
			perMTok(out, p.OutputPerMTok)))
	}
	return strings.Join(parts, "  ")
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

// lookupPrice returns the rate card for a model, or nil when none is stated.
//
// Read on every call rather than cached: editing the file is how a user
// corrects a rate, and a cost readout that keeps quoting the old one until
// restart is the same staleness problem by another route.
func lookupPrice(model string) *modelPrice {
	path := priceFilePath()
	if path == "" {
		return nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var table map[string]modelPrice
	if err := json.Unmarshal(raw, &table); err != nil {
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
