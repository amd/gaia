// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import "testing"

// The agent confirms a switch with its own spelling of the id; a strict
// compare read that as "not the model asked for" and never saved the switch.
func TestSameModelID(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"Gemma-4-E4B-it-GGUF", "gemma-4-e4b-it-gguf", true},
		{"user.My-Model-GGUF", "My-Model-GGUF", true},
		{"fireworks.GLM-5p3-flash", "fireworks.glm-5p3-flash", true},
		{"Gemma-4-E4B-it-GGUF", "gemma4-it-e4b-FLM", false},
		{"", "", false},
		{"", "Gemma-4-E4B-it-GGUF", false},
	}
	for _, c := range cases {
		if got := sameModelID(c.a, c.b); got != c.want {
			t.Errorf("sameModelID(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}
