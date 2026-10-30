// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/event"
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

func audioResult(card string, extra string) event.CanonicalToolResultEvent {
	return event.CanonicalToolResultEvent{
		Preview: "Saved music_1.wav (12.0 s) · 95321ms",
		Data:    []byte(`{"summary":"Saved music_1.wav (12.0 s)","success":true,"status":"success"` + extra + `,"generated_audio":` + card + `}`),
	}
}

func TestGeneratedAudioResultDetail(t *testing.T) {
	withPaths(t, `C:\work\game`, `C:\Users\me`)
	for _, tc := range []struct {
		name string
		e    event.CanonicalToolResultEvent
		want string
	}{
		{"music in the project folder",
			audioResult(`{"path":"C:\\work\\game\\audio\\theme.wav","model":"ACE-Step-Music","kind":"music","duration_seconds":12.04,"duration_requested":12}`, `,"latency_ms":95321`),
			`Music saved · ACE-Step-Music · 12.0 s · 95.3s · audio\theme.wav`},
		{"sound effect falls back to the requested length",
			audioResult(`{"path":"C:\\Users\\me\\.gaia\\cache\\audio\\generations\\sfx_1.wav","model":"ThinkSound-SFX","kind":"sfx","duration_seconds":null,"duration_requested":3}`, ``),
			`Sound effect saved · ThinkSound-SFX · 3 s · ~\.gaia\cache\audio\generations\sfx_1.wav`},
		{"posix path, no length",
			audioResult(`{"path":"/srv/out/x.WAV","model":"ACE-Step-Music","kind":"music"}`, ``),
			`Music saved · ACE-Step-Music · /srv/out/x.WAV`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := toolResultDetail(tc.e); got != tc.want {
				t.Errorf("toolResultDetail = %q, want %q", got, tc.want)
			}
		})
	}
}

// Anything but a successful, absolute .wav result falls through to the
// generic outcome line: nothing unverified may read as "saved".
func TestGeneratedAudioResultDetailRefusesBadPayloads(t *testing.T) {
	const preview = "Saved music_1.wav (12.0 s) · 95321ms"
	for _, tc := range []struct{ name, card string }{
		{"missing path", `{"model":"ACE-Step-Music","kind":"music"}`},
		{"empty path", `{"path":""}`},
		{"not a wav", `{"path":"C:\\x\\song.mp3"}`},
		{"a program", `{"path":"C:\\x\\run.wav.exe"}`},
		{"relative", `{"path":"audio\\theme.wav"}`},
		{"traversal", `{"path":"C:\\x\\..\\..\\Windows\\a.wav"}`},
		{"ansi escape", `{"path":"C:\\x\\a\u001b]0;pwned\u0007.wav"}`},
		{"newline", `{"path":"C:\\x\\a\nb.wav"}`},
		{"not an object", `"C:\\x\\a.wav"`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := toolResultDetail(audioResult(tc.card, ``))
			if strings.Contains(got, "saved") || got != preview {
				t.Errorf("toolResultDetail = %q, want the generic preview %q", got, preview)
			}
		})
	}

	t.Run("a failure stays a failure", func(t *testing.T) {
		e := event.CanonicalToolResultEvent{
			Preview: "error: Lemonade audio generation failed (503)",
			Data:    []byte(`{"success":false,"summary":"error: Lemonade audio generation failed (503)","generated_audio":{"path":"C:\\x\\a.wav"}}`),
		}
		got := toolResultDetail(e)
		if !strings.HasPrefix(got, "failed — ") || strings.Contains(got, "saved") {
			t.Errorf("toolResultDetail = %q", got)
		}
	})

	t.Run("an oversized path keeps its file name", func(t *testing.T) {
		long := `C:\\` + strings.Repeat(`deep\\`, 500) + `deep_theme.wav`
		got := toolResultDetail(audioResult(`{"path":"`+long+`","model":"ACE-Step-Music","kind":"music"}`, ``))
		if !strings.HasPrefix(got, "Music saved · ACE-Step-Music · ") || !strings.HasSuffix(got, "deep_theme.wav") {
			t.Errorf("toolResultDetail = %.80q", got)
		}
		if displayWidth(got) > detailMax {
			t.Errorf("width %d > detailMax %d", displayWidth(got), detailMax)
		}
	})

	t.Run("another tool's result is untouched", func(t *testing.T) {
		e := event.CanonicalToolResultEvent{Preview: "3 files", Data: []byte(`{"files":[1,2,3]}`)}
		if got := toolResultDetail(e); got != "3 files" {
			t.Errorf("toolResultDetail = %q", got)
		}
	})
}
