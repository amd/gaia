// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

// Pick is a Fireworks model GAIA has measured end to end, so the picker can
// say what it costs and what it is good at instead of listing a bare id.
type Pick struct {
	// ID is the Lemonade model id, as discovery reports it.
	ID string
	// Name is what a human reads.
	Name string
	// Note is one line: cost tier and what the model is good at.
	Note string
	// Bench is the measured evidence, one line.
	Bench string
}

// Benchmarked lists the Fireworks models measured with the GAIA flagship
// agent, in display order: 14 everyday coding tasks (mean of 3 runs) and 4
// real ROCm build-system bugs in a 144K-line repo (TheRock, 1 run), each
// judged by an independent Opus 5 judge and billed at real Fireworks rates.
//
// Ordered, not a map, because it is rendered. Discovery still decides what is
// offered: a pick the account does not expose is never shown, and the
// suggested FireworksModel keeps its place ahead of these.
var Benchmarked = []Pick{
	{
		ID:    "fireworks.glm-5p3-flash",
		Name:  "GLM-5.3 Flash",
		Note:  "Cheapest per task; best value for everyday coding",
		Bench: "14/14 tasks · quality 4.89/5 · $0.09 per 14-task run · TheRock 4/4, 3.69/5, $0.42",
	},
	{
		ID:    "fireworks.deepseek-v4p1-flash",
		Name:  "DeepSeek V4.1 Flash",
		Note:  "Highest quality of the cheap tier; re-sent context is near-free",
		Bench: "14/14 tasks · quality 4.92/5 · $0.10 per 14-task run · TheRock 4/4, 3.81/5, $0.75",
	},
	{
		ID:    "fireworks.kimi-k2p7-code",
		Name:  "Kimi K2.7 Code",
		Note:  "Strongest on large codebases; about 4-9x the cost",
		Bench: "14/14 tasks · quality 4.68/5 · $0.41 per 14-task run · TheRock 4/4, 4.00/5, $3.71",
	},
}

// BenchmarkedPick returns the entry for id, and whether it is one.
func BenchmarkedPick(id string) (Pick, bool) {
	for _, p := range Benchmarked {
		if p.ID == id {
			return p, true
		}
	}
	return Pick{}, false
}

// PickRank orders a discovered catalog: the suggested model first, then the
// benchmarked picks in their own order, then everything else.
func PickRank(id string) int {
	if id == FireworksModel {
		return 0
	}
	for i, p := range Benchmarked {
		if p.ID == id {
			return i + 1
		}
	}
	return len(Benchmarked) + 1
}
