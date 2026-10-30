// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
)

// Music and sound effects take minutes, so their work-log rows say what is
// being made and for how long, and the result row names the saved WAV with
// the model and length — the things a user needs to find and judge it.

// audioDurationMax rejects a duration no request could mean (over a day).
const audioDurationMax = 24 * 3600

// audioSeconds renders a requested or measured length: "12 s", "2.5 s".
// "" for a missing, non-positive, non-finite or absurd value.
func audioSeconds(v interface{}) string {
	f, ok := floatOf(v)
	if !ok || math.IsNaN(f) || math.IsInf(f, 0) || f <= 0 || f > audioDurationMax {
		return ""
	}
	if f == math.Trunc(f) {
		return fmt.Sprintf("%d s", int64(f))
	}
	return fmt.Sprintf("%.1f s", f)
}

// audioNarration is the call row for the three audio tools; ok is false for
// any other tool. The sidecar's own narration still wins when it sends one.
func audioNarration(tool string, args json.RawMessage) (string, bool) {
	var line string
	obj := decodeObject(args)
	seconds := ""
	if obj != nil {
		seconds = audioSeconds(obj["duration"])
	}
	switch tool {
	case "list_audio_generation_models":
		return "Checking which audio models are downloaded", true
	case "generate_music":
		line = "Composing music"
		if seconds != "" {
			line = "Composing " + seconds + " of music"
		}
	case "generate_sound_effect":
		line = "Generating a sound effect"
		if seconds != "" {
			line = "Generating a " + seconds + " sound effect"
		}
	default:
		return "", false
	}
	if prompt := argValue(args, "prompt"); prompt != "" {
		room := narrationMax - displayWidth(line) - 2
		line += ": " + truncateRunes(prompt, room)
	}
	return line, true
}

// generatedAudioDetail is the result row for a generated WAV: "Music saved ·
// ACE-Step-Music · 12 s · <path>". ok is false when the result is not a
// successful generated_audio payload with an absolute .wav path, so the
// generic outcome line renders instead and nothing unverified reads as saved.
func generatedAudioDetail(raw json.RawMessage) (string, bool) {
	data := decodeObject(raw)
	if data == nil {
		return "", false
	}
	audio, ok := data["generated_audio"].(map[string]interface{})
	if !ok {
		return "", false
	}
	path, _ := audio["path"].(string)
	if !isGeneratedWavPath(path) {
		return "", false
	}
	label := "Music saved"
	if stringOf(audio["kind"]) == "sfx" {
		label = "Sound effect saved"
	}
	parts := []string{label}
	if model := clean(stringOf(audio["model"])); model != "" {
		parts = append(parts, model)
	}
	seconds := audioSeconds(audio["duration_seconds"])
	if seconds == "" {
		seconds = audioSeconds(audio["duration_requested"])
	}
	if seconds != "" {
		parts = append(parts, seconds)
	}
	if latency := latencyText(data["latency_ms"]); latency != "" {
		parts = append(parts, latency)
	}
	// The file name is the part worth keeping, so an oversized path loses its head.
	head := strings.Join(parts, " · ") + " · "
	if room := detailMax - displayWidth(head); len([]rune(path)) > room {
		runes := []rune(path)
		path = "…" + string(runes[len(runes)-room+1:])
	}
	return head + path, true
}

// isGeneratedWavPath accepts an absolute .wav path with nothing a terminal
// would act on: no escape or control byte, no ".." segment.
func isGeneratedWavPath(p string) bool {
	if p == "" || len(p) > 4096 || clean(p) != p {
		return false
	}
	for _, r := range p {
		if r < 0x20 || r == 0x7f {
			return false
		}
	}
	if !strings.HasSuffix(strings.ToLower(p), ".wav") {
		return false
	}
	for _, seg := range strings.FieldsFunc(p, func(r rune) bool { return r == '/' || r == '\\' }) {
		if seg == ".." {
			return false
		}
	}
	drive := len(p) >= 3 && ((p[0] >= 'a' && p[0] <= 'z') || (p[0] >= 'A' && p[0] <= 'Z')) &&
		p[1] == ':' && (p[2] == '\\' || p[2] == '/')
	return drive || strings.HasPrefix(p, "/") || strings.HasPrefix(p, `\\`)
}
