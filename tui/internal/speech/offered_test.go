// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"strings"
	"testing"
)

func TestReadAloudIsWithheldWhereTheVoiceIsUnproven(t *testing.T) {
	for _, goos := range []string{"windows", "linux"} {
		if got := UnprovenReason(goos); got != "" {
			t.Errorf("UnprovenReason(%q) = %q, want offered", goos, got)
		}
	}
	got := UnprovenReason("darwin")
	for _, want := range []string{"aren't offered on macOS", "Windows and Linux", "issues/8831"} {
		if !strings.Contains(got, want) {
			t.Errorf("UnprovenReason(darwin) = %q, missing %q", got, want)
		}
	}
}

func TestCheckLengthRefusesSpeechCutToHalfASecond(t *testing.T) {
	text := strings.TrimSpace(strings.Repeat("one two three four five six ", 5)) // 30 words
	err := CheckLength(text, 0.48, 1.0)
	if err == nil {
		t.Fatal("0.48 s for 30 words was accepted")
	}
	for _, want := range []string{"0.5 s of audio for 30 words", "at least 10 s", "update Lemonade Server"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not say %q", err, want)
		}
	}
}

func TestCheckLengthAcceptsWholeSpeechShortTextAndFastSpeed(t *testing.T) {
	text := strings.TrimSpace(strings.Repeat("one two three four five six ", 5))
	if err := CheckLength(text, 12, 1.0); err != nil {
		t.Errorf("12 s for 30 words was refused: %v", err)
	}
	if err := CheckLength("Hello there, how are you?", 0.1, 1.0); err != nil {
		t.Errorf("short text was length-checked: %v", err)
	}
	// At double speed the floor halves: 30 words need 5 s, so 1.5 s passes.
	if err := CheckLength(text, 1.5, 2.0); err != nil {
		t.Errorf("1.5 s at 2x for 30 words was refused: %v", err)
	}
}
