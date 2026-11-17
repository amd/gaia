// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package hybrid

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/amd/gaia/tui/internal/event"
)

// kindLabels are the classifier's request kinds in plain words; a kind not
// listed is shown as the backend names it.
var kindLabels = map[string]string{
	"write_prose":     "writing",
	"answer_question": "question",
	"analyze_data":    "data analysis",
	"operate_tools":   "using tools",
	"change_code":     "code change",
	"find_defects":    "finding bugs",
	"plan_or_design":  "planning",
	"other":           "other",
}

func kindLabel(kind string) string {
	if l, ok := kindLabels[kind]; ok {
		return l
	}
	return kind
}

// providerLabels name the backend's providers for a group heading.
var providerLabels = map[string]string{
	"local":     "On this PC",
	"amd":       "AMD LLM Gateway",
	"fireworks": "Fireworks AI",
	"claude":    "Anthropic (Claude)",
}

func providerLabel(p string) string {
	if l, ok := providerLabels[p]; ok {
		return l
	}
	return p
}

var nameParts = regexp.MustCompile(`[-_]`)

// shortName is a model's family for a one-line label, so amd.Claude-Opus-5-5
// is Opus and amd.DeepSeek-V4.1-Flash is DeepSeek.
func shortName(id string) string {
	bare := strings.TrimSpace(id)
	for _, prefix := range []string{"amd.", "fireworks."} {
		bare = strings.TrimPrefix(bare, prefix)
	}
	bare = strings.TrimPrefix(bare, "user.")
	bare = bare[strings.LastIndex(bare, "/")+1:]
	bare = strings.TrimPrefix(bare, "claude-")
	first := bare
	for _, part := range nameParts.Split(bare, -1) {
		if part != "" && strings.ToLower(part) != "claude" {
			first = part
			break
		}
	}
	if first == "" {
		return first
	}
	return strings.ToUpper(first[:1]) + first[1:]
}

// RouteLine is a route in a few plain words: "→ DeepSeek (writing, d1)".
func RouteLine(r event.CanonicalRoute) string {
	name := shortName(r.Model)
	switch {
	case r.Sticky:
		return "→ " + name + " (kept on it after an earlier hard turn)"
	case !r.Classified:
		return "→ " + name + " (couldn't classify the request)"
	}
	why := []string{kindLabel(r.Kind)}
	if r.Difficulty != nil && *r.Difficulty > 0 {
		why = append(why, fmt.Sprintf("d%d", *r.Difficulty))
	}
	if r.Stakes == "high" {
		why = append(why, "high stakes")
	}
	return "→ " + name + " (" + strings.Join(why, ", ") + ")"
}

// PreviewLine is a preview's route with how long the classifier took.
func PreviewLine(r event.CanonicalRoute) string {
	line := RouteLine(r)
	if r.Seconds > 0 {
		line += fmt.Sprintf(" · %.1fs", r.Seconds)
	}
	return line
}
