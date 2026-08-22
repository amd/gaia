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

// minGenerationShare is the other half of the test, and the one that actually
// discriminates. A turn that spends 23.6s of its 24.8s before the first token
// did not stream — the 1.2s left over clears the absolute floor above while
// still being scheduling noise, and published "556.6 tok/s" for a model that
// really runs at about 44. Streaming turns spend a large fraction of the turn
// generating; single-frame turns spend almost none.
const minGenerationShare = 0.2

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
	if duration > 0 && window.Seconds()/duration.Seconds() < minGenerationShare {
		return 0, false
	}
	return float64(tokens) / window.Seconds(), true
}
