// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestAudioToolNarration(t *testing.T) {
	long := strings.Repeat("piano ", 2000)
	for _, tc := range []struct {
		name, tool, args, narration, want string
	}{
		{"music with length and prompt", "generate_music", `{"duration":12,"prompt":"lo-fi piano"}`, "", "Composing 12 s of music: lo-fi piano"},
		{"fractional length", "generate_music", `{"duration":2.5}`, "", "Composing 2.5 s of music"},
		{"no length", "generate_music", `{"prompt":"jazz"}`, "", "Composing music: jazz"},
		{"sound effect", "generate_sound_effect", `{"duration":3,"prompt":"glass shatters"}`, "", "Generating a 3 s sound effect: glass shatters"},
		{"sound effect, no args", "generate_sound_effect", ``, "", "Generating a sound effect"},
		{"model list", "list_audio_generation_models", `{}`, "", "Checking which audio models are downloaded"},
		{"zero length is dropped", "generate_music", `{"duration":0}`, "", "Composing music"},
		{"negative length is dropped", "generate_music", `{"duration":-4}`, "", "Composing music"},
		{"absurd length is dropped", "generate_music", `{"duration":1e9}`, "", "Composing music"},
		{"string length is dropped", "generate_music", `{"duration":"12"}`, "", "Composing music"},
		{"bool length is dropped", "generate_sound_effect", `{"duration":true}`, "", "Generating a sound effect"},
		{"escape in prompt is stripped", "generate_music", `{"prompt":"a\u001b[31mred\u001b[0m song"}`, "", "Composing music: ared song"},
		{"sidecar narration still wins", "generate_music", `{"duration":12}`, "Composing 12 s of music: x", "Composing 12 s of music: x"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := toolNarration(tc.tool, json.RawMessage(tc.args), tc.narration); got != tc.want {
				t.Errorf("toolNarration(%q, %s) = %q, want %q", tc.tool, tc.args, got, tc.want)
			}
		})
	}
	t.Run("a long prompt is clipped to the log bound", func(t *testing.T) {
		got := toolNarration("generate_music", json.RawMessage(`{"duration":30,"prompt":"`+long+`"}`), "")
		if !strings.HasPrefix(got, "Composing 30 s of music: piano") || displayWidth(got) > narrationMax {
			t.Errorf("got %d cols: %.60q", displayWidth(got), got)
		}
	})
}
