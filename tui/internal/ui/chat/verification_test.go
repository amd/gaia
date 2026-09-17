// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "testing"

func TestStripVerificationScopeRemovesTrailingLine(t *testing.T) {
	in := "Four. Simple enough for anyone.\n\nVerification: unverified — no tools ran, so nothing was checked."
	got := stripVerificationScope(in)
	want := "Four. Simple enough for anyone."
	if got != want {
		t.Errorf("stripVerificationScope(%q) = %q, want %q", in, got, want)
	}
}

func TestStripVerificationScopeRemovesRepeatedLines(t *testing.T) {
	// Regression: a queued/auto-drained turn was observed appending the scope
	// line twice — both copies must go, not just the last one.
	in := "The joke.\n\nVerification: unverified — no tools ran, so nothing was checked." +
		"\n\nVerification: unverified — no tools ran, so nothing was checked."
	got := stripVerificationScope(in)
	want := "The joke."
	if got != want {
		t.Errorf("stripVerificationScope(%q) = %q, want %q", in, got, want)
	}
}

func TestStripVerificationScopeLeavesOrdinaryTextAlone(t *testing.T) {
	in := "No scope line here at all."
	if got := stripVerificationScope(in); got != in {
		t.Errorf("stripVerificationScope(%q) = %q, want unchanged", in, got)
	}
}

func TestStripVerificationScopeLeavesMidTextMentionAlone(t *testing.T) {
	// Only a trailing scope line is a verification footer; the same text
	// mid-answer is the model talking about verification, not the footer.
	in := "Verification: is the step where you confirm a fix works. Do that before shipping."
	if got := stripVerificationScope(in); got != in {
		t.Errorf("stripVerificationScope(%q) = %q, want unchanged", in, got)
	}
}

func TestStripVerificationScopeHandlesPartiallyVerified(t *testing.T) {
	in := "Done.\n\nVerification: partially verified — lint ran and passed; run_tests failed."
	got := stripVerificationScope(in)
	want := "Done."
	if got != want {
		t.Errorf("stripVerificationScope(%q) = %q, want %q", in, got, want)
	}
}
