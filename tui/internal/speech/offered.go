// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"fmt"
	"strings"
)

// testedPlatforms are where the Kokoro voice is proven to speak whole replies.
// Mirrors gaia.config.GaiaConfig.TESTED_SPEECH_OUTPUT_PLATFORMS.
var testedPlatforms = []string{"windows", "linux"}

var platformNames = map[string]string{"darwin": "macOS", "windows": "Windows", "linux": "Linux"}

// UnprovenReason says why read-aloud isn't offered on goos (a runtime.GOOS
// value), or "" where it is. Mirrors gaia.config.speech_output_unproven_reason.
func UnprovenReason(goos string) string {
	for _, p := range testedPlatforms {
		if p == goos {
			return ""
		}
	}
	name := platformNames[goos]
	if name == "" {
		name = goos
	}
	proven := make([]string, len(testedPlatforms))
	for i, p := range testedPlatforms {
		proven[i] = platformNames[p]
	}
	return fmt.Sprintf("Reading aloud and dictation aren't offered on %s yet, because GAIA "+
		"hasn't verified its local voice there. They work on %s; follow "+
		"https://github.com/amd/gaia/issues/8831 for %s.", name, strings.Join(proven, " and "), name)
}

const (
	// maxWordsPerSecond is faster than any voice speaks.
	maxWordsPerSecond = 3.0
	// truncatedFraction of the fastest estimate is the least speech accepted.
	truncatedFraction = 0.25
	// minWordsToCheck: shorter text is not checked, as pauses skew it.
	minWordsToCheck = 10
)

// CheckLength fails when seconds of speech is far too short to have said
// text at speed (1.0 is normal), as when the speech runtime cuts every reply
// to a fraction of a second. Mirrors gaia.audio.lemonade_tts.check_speech_length.
func CheckLength(text string, seconds, speed float64) error {
	words := len(strings.Fields(text))
	if words < minWordsToCheck {
		return nil
	}
	if speed < 0.01 {
		speed = 0.01
	}
	fastest := float64(words) / (maxWordsPerSecond * speed)
	if seconds >= fastest*truncatedFraction {
		return nil
	}
	return fmt.Errorf("Lemonade's speech model returned %.1f s of audio for %d words, which take "+
		"at least %.0f s to say, so the speech is cut off and GAIA did not use it. This is a fault "+
		"in Lemonade's text-to-speech runtime: update Lemonade Server and try again, and check its "+
		"log for the speech request. Known issue: https://github.com/amd/gaia/issues/8831",
		seconds, words, fastest)
}
