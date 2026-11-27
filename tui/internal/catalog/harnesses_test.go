// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package catalog

import "testing"

// The two names the TUI shows come from the shared catalog, so they cannot
// drift from the Agent UI's wording.
func TestHarnessLabelNamesTheTwoFlagshipPrograms(t *testing.T) {
	for name, want := range map[string]string{
		PythonAgentChoice: "Full-featured",
		NativeAgentBinary: "Fast and light",
	} {
		if got := HarnessLabel(name); got != want {
			t.Errorf("HarnessLabel(%q) = %q, want %q", name, got, want)
		}
	}
}

// A name this build does not know is still named, rather than shown empty.
func TestHarnessLabelKeepsAnUnknownName(t *testing.T) {
	if got := HarnessLabel("unknown"); got != "unknown" {
		t.Errorf("HarnessLabel(unknown) = %q, want %q", got, "unknown")
	}
}
