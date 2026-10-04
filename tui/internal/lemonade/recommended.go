// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	_ "embed"
	"encoding/json"
	"fmt"
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

	// Registration for a model that is not a Lemonade built-in. Empty for
	// built-ins, which Lemonade pulls by name.
	RegisterAs string  `json:"register_as"`
	SizeGB     float64 `json:"size_gb"`
	Checkpoint string  `json:"checkpoint"`
	Recipe     string  `json:"recipe"`
	MMProj     string  `json:"mmproj"`
	Vision     bool    `json:"vision"`
	Reasoning  bool    `json:"reasoning"`
	// MinLemonade is the oldest Lemonade whose llama.cpp can load the model.
	MinLemonade string `json:"min_lemonade_version"`
	// MinCtxSize is the window GAIA loads the model with at the least, and
	// KVBytesPerToken its KV cache per token of window; 0 when the fit rule's
	// shared margin covers the cache.
	MinCtxSize      int64 `json:"min_ctx_size"`
	KVBytesPerToken int64 `json:"kv_bytes_per_token"`
}

// KVCacheGB is the KV cache at the model's floor window. GAIA grows the window
// past the floor only into memory left over, so a fit judged here is the fit
// `gaia init` judges at the window it picks.
func (r Recommended) KVCacheGB() float64 {
	return float64(r.KVBytesPerToken) * float64(r.MinCtxSize) / 1e9
}

// Matches reports whether a catalog id is this recommendation. A cloud id also
// matches its account-path form (fireworks.accounts/fireworks/models/<name>).
func (r Recommended) Matches(id string) bool {
	if id == r.ID || (r.RegisterAs != "" && id == r.RegisterAs) {
		return true
	}
	return r.Provider != "local" && rankKey(id) == rankKey(r.ID)
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
	// The ranked Fireworks list with its Evidence is RecommendedModels in
	// cloud.go; this JSON's Fireworks entries only label and group those rows in
	// the picker, and TestFireworksRecommendationsMatchTheRanking keeps the two
	// in the same order.
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
