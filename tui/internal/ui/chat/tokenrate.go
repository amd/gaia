// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "time"

// minGenerationWindow is the shortest gap between the first token and the end of
// a turn that can describe generation rather than one frame's arrival.
//
// TTFT is stamped when the first token event lands. If the answer streamed, that
// is early and `Duration - TTFT` is the time the model spent generating. If the
// answer arrived whole in a single frame — a non-streaming provider, or a reply
// short enough to finish inside one — TTFT lands at the very END of the turn and
// the remaining sliver is scheduling noise.
//
// A local Gemma-4-E4B turn measured 219 tokens with ttft 46.2s of a 46.3s turn,
// and the old arithmetic published "2142.6 tok/s" off the 0.1s left over. That
// number is roughly twenty times the hardware's real rate.
const minGenerationWindow = time.Second

// tokensPerSecond reports the generation rate, and whether one was measurable.
//
// Returning ok=false rather than a number is the point: a rate computed from a
// window too short to contain the generation is invented, and an invented figure
// in a performance readout is worse than a missing one.
func tokensPerSecond(tokens int, duration, ttft time.Duration) (float64, bool) {
	if tokens <= 0 {
		return 0, false
	}
	window := duration - ttft
	if window < minGenerationWindow {
		return 0, false
	}
	return float64(tokens) / window.Seconds(), true
}
