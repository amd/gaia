// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "regexp"

// verificationScopeRE matches a trailing "Verification: unverified ..." line
// the agent loop appends when a turn ran no checks at all (see
// gaia/agents/base/verification.py, build_verification_scope). That specific
// case is pure noise — the same disclaimer on every conversational turn,
// duplicating nothing the turn footer says either. A "verified" or "partially
// verified" line is left alone: it is the only place a failed or skipped
// check is reported, and the footer (duration, tokens, steps) never carries
// that signal.
//
// Kept in sync by hand with Python's VERIFICATION_SCOPE_PREFIX /
// _SCOPE_LINE_RE — see the matching note on strip_verification_scope there.
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
