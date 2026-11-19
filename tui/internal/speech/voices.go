// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package speech reads replies aloud: markdown to speakable pieces, Lemonade's
// Kokoro voices, and playback through the operating system's audio player.
package speech

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
)

// Voice is one Kokoro voice Lemonade's kokoro-v1 accepts.
type Voice struct {
	ID   string `json:"id"`
	Name string `json:"name"` // "American Female - Bella"
}

// Short is the voice's own name, "Bella".
func (v Voice) Short() string {
	if _, after, ok := strings.Cut(v.Name, " - "); ok {
		return after
	}
	return v.Name
}

// The voice list lives in voices.json and must match Python's
// gaia.audio.lemonade_tts.KOKORO_VOICES; tests/unit/test_tui_voices_drift.py
// fails when the two drift.
//
//go:embed voices.json
var voicesJSON []byte

var catalog = mustParseVoices(voicesJSON)

type voiceFile struct {
	Default string  `json:"default"`
	Voices  []Voice `json:"voices"`
}

func mustParseVoices(raw []byte) voiceFile {
	var f voiceFile
	if err := json.Unmarshal(raw, &f); err != nil {
		panic(fmt.Sprintf("speech: voices.json is invalid: %v", err))
	}
	if _, ok := lookup(f.Voices, f.Default); !ok {
		panic(fmt.Sprintf("speech: voices.json default %q is not in its voice list", f.Default))
	}
	return f
}

func lookup(voices []Voice, id string) (Voice, bool) {
	for _, v := range voices {
		if v.ID == id {
			return v, true
		}
	}
	return Voice{}, false
}

// DefaultVoice is the voice used when none is saved.
func DefaultVoice() string { return catalog.Default }

// Voices is every voice, in the catalog's order.
func Voices() []Voice { return append([]Voice(nil), catalog.Voices...) }

// LookupVoice finds a voice by id.
func LookupVoice(id string) (Voice, bool) { return lookup(catalog.Voices, id) }

// VoiceGroup is the voices of one accent and gender.
type VoiceGroup struct {
	Label  string
	Voices []Voice
}

// VoiceGroups splits the voices the way a picker shows them. The id says it:
// the first letter is the accent (a/b), the second the gender (f/m).
func VoiceGroups() []VoiceGroup {
	groups := []VoiceGroup{
		{Label: "American English, female"},
		{Label: "American English, male"},
		{Label: "British English, female"},
		{Label: "British English, male"},
	}
	for _, v := range catalog.Voices {
		i := -1
		switch {
		case strings.HasPrefix(v.ID, "af_"):
			i = 0
		case strings.HasPrefix(v.ID, "am_"):
			i = 1
		case strings.HasPrefix(v.ID, "bf_"):
			i = 2
		case strings.HasPrefix(v.ID, "bm_"):
			i = 3
		}
		if i < 0 {
			panic(fmt.Sprintf("speech: voice %q has no a/b accent and f/m gender prefix", v.ID))
		}
		groups[i].Voices = append(groups[i].Voices, v)
	}
	return groups
}

// Speed limits, as a percentage of normal. Mirrors gaia.config's
// READ_ALOUD_MIN_SPEED / READ_ALOUD_MAX_SPEED.
const (
	MinSpeed     = 50
	MaxSpeed     = 200
	DefaultSpeed = 100
)
