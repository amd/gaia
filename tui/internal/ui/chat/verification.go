// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "regexp"

// verificationScopeRE matches a trailing "Verification: unverified ..." line,
// which older agent versions appended when a turn ran no checks at all. The
// agent now says nothing in that case (gaia/agents/base/verification.py,
// build_verification_scope), so this only cleans answers from saved sessions.
// A "verified" or "partially verified" line is left alone: it is the only
// place a failed or skipped check was reported.
var verificationScopeRE = regexp.MustCompile(`\n{1,2}Verification: unverified[^\n]*\s*$`)

// StripVerificationScope removes a trailing no-op "Verification: unverified
// ..." line. It loops because a turn can append more than one (e.g. a queued
// turn that re-finalized text which already carried it), and a single pass
// only ever removes the last one.
func StripVerificationScope(text string) string {
	for {
		stripped := verificationScopeRE.ReplaceAllString(text, "")
		if stripped == text {
			return text
		}
		text = stripped
	}
}
