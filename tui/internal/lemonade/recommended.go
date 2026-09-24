// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
)

// Recommended is a model GAIA points users at ahead of the rest of Lemonade's
// catalog. The list lives in recommended_models.json; the Python side
// (gaia.llm.lemonade_client.MODELS, gaia.llm.model_fit) must agree with it, and
// tests/unit/test_model_fit.py fails when the two drift.
type Recommended struct {
	ID       string `json:"id"`
	Provider string `json:"provider"`
	Label    string `json:"label"`
	Note     string `json:"note"`
	// MatchPrefix makes ID a prefix: a Fireworks deployment's id carries a
	// suffix GAIA cannot know in advance.
	MatchPrefix bool `json:"match_prefix"`

	// Registration for a model that is not a Lemonade built-in. Empty for
	// built-ins, which Lemonade pulls by name.
	RegisterAs string  `json:"register_as"`
	SizeGB     float64 `json:"size_gb"`
	Checkpoint string  `json:"checkpoint"`
	Recipe     string  `json:"recipe"`
	MMProj     string  `json:"mmproj"`
	Vision     bool    `json:"vision"`
	Reasoning  bool    `json:"reasoning"`
}

// Matches reports whether a catalog id is this recommendation.
func (r Recommended) Matches(id string) bool {
	if r.MatchPrefix {
		return strings.HasPrefix(id, r.ID)
	}
	return id == r.ID || (r.RegisterAs != "" && id == r.RegisterAs)
}

type fitConstants struct {
	MemoryOverheadFactor float64 `json:"memory_overhead_factor"`
	MemoryOverheadGB     float64 `json:"memory_overhead_gb"`
}

//go:embed recommended_models.json
var recommendedJSON []byte

var (
	recommended []Recommended
	fitRule     fitConstants
)

func init() {
	var doc struct {
		Fit    fitConstants  `json:"fit"`
		Models []Recommended `json:"models"`
	}
	if err := json.Unmarshal(recommendedJSON, &doc); err != nil {
		panic(fmt.Sprintf("recommended_models.json is invalid: %v", err))
	}
	if doc.Fit.MemoryOverheadFactor <= 0 {
		panic("recommended_models.json: fit.memory_overhead_factor must be positive")
	}
	recommended, fitRule = doc.Models, doc.Fit
}

// RecommendedFor returns the recommendations for one provider, in list order.
func RecommendedFor(provider string) []Recommended {
	var out []Recommended
	for _, r := range recommended {
		if r.Provider == provider {
			out = append(out, r)
		}
	}
	return out
}
