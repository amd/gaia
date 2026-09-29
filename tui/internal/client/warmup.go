// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

// The warm-up sentinel: sent before the chat opens so the first question does
// not pay for starting the agent, loading the model and reading the system
// prompt. Pinned by tests/fixtures/stdio/gaia_stdio_wire.json.
const (
	WarmUpQuery = "\x00gaia:warm_up\x00"
	// WarmedUp is the `final` answer when the model is loaded and primed.
	WarmedUp = "warmed_up"
	// WarmUpSkipped is the `final` answer for a remote model: nothing loads
	// locally, and a priming call would only cost money.
	WarmUpSkipped = "warm_up_skipped"
)

// WarmUpper is implemented by transports whose agent understands WarmUpQuery.
// Anything else would receive it as a question.
type WarmUpper interface {
	SupportsWarmUp() bool
}

// SupportsWarmUp reports whether the child speaks the canonical stdio protocol
// of the flagship agent, the only one that answers WarmUpQuery.
func (s *SubprocessClient) SupportsWarmUp() bool { return s.canonical }
