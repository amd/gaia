// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "testing"

func TestStripVerificationScopeRemovesTrailingUnverifiedLine(t *testing.T) {
	in := "Four. Simple enough for anyone.\n\nVerification: unverified — no tools ran, so nothing was checked."
	got := StripVerificationScope(in)
	want := "Four. Simple enough for anyone."
	if got != want {
		t.Errorf("StripVerificationScope(%q) = %q, want %q", in, got, want)
	}
}

func TestStripVerificationScopeRemovesRepeatedUnverifiedLines(t *testing.T) {
	// Regression: a queued/auto-drained turn was observed appending the scope
	// line twice — both copies must go, not just the last one.
	in := "The joke.\n\nVerification: unverified — no tools ran, so nothing was checked." +
		"\n\nVerification: unverified — no tools ran, so nothing was checked."
	got := StripVerificationScope(in)
	want := "The joke."
	if got != want {
		t.Errorf("StripVerificationScope(%q) = %q, want %q", in, got, want)
	}
}

func TestStripVerificationScopeLeavesOrdinaryTextAlone(t *testing.T) {
	in := "No scope line here at all."
	if got := StripVerificationScope(in); got != in {
		t.Errorf("StripVerificationScope(%q) = %q, want unchanged", in, got)
	}
}

func TestStripVerificationScopeLeavesMidTextMentionAlone(t *testing.T) {
	// Only a trailing scope line is a verification footer; the same text
	// mid-answer is the model talking about verification, not the footer.
	in := "Verification: is the step where you confirm a fix works. Do that before shipping."
	if got := StripVerificationScope(in); got != in {
		t.Errorf("StripVerificationScope(%q) = %q, want unchanged", in, got)
	}
}

// A "verified" or "partially verified" line is the only place the turn
// reports a check that actually ran (and whether it passed) — the footer
// (duration, tokens, steps) never carries that. Only the no-op "unverified"
// case is noise; these two must survive the strip.
func TestStripVerificationScopeKeepsPartiallyVerified(t *testing.T) {
	in := "Done.\n\nVerification: partially verified — lint ran and passed; run_tests failed."
	if got := StripVerificationScope(in); got != in {
		t.Errorf("StripVerificationScope(%q) = %q, want unchanged (a failed check is real signal)", in, got)
	}
}

func TestStripVerificationScopeKeepsFullyVerified(t *testing.T) {
	in := "Done.\n\nVerification: verified — pytest ran and passed."
	if got := StripVerificationScope(in); got != in {
		t.Errorf("StripVerificationScope(%q) = %q, want unchanged", in, got)
	}
}
