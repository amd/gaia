// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "regexp"

// verificationScopeRE matches a trailing "Verification: ..." line the agent
// loop appends to every answer (see gaia/agents/base/verification.py). The
// TUI already shows its own turn footer (duration, tokens, steps), so this
// line is redundant noise here — strip it before rendering.
var verificationScopeRE = regexp.MustCompile(`\n{1,2}Verification: [^\n]*\s*$`)

// stripVerificationScope removes a trailing verification-scope line. It loops
// because a turn can append more than one (e.g. a turn that goes through more
// than one exit path), and a single pass only ever removes the last one.
func stripVerificationScope(text string) string {
	for {
		stripped := verificationScopeRE.ReplaceAllString(text, "")
		if stripped == text {
			return text
		}
		text = stripped
	}
}
