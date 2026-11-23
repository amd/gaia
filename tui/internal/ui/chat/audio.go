// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"math"
)

// Music and sound effects take minutes, so their work-log rows say what is
// being made and for how long.

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
