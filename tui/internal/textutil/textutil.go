// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package textutil holds plain-text formatting the setup screens share, so one
// size never prints two ways.
package textutil

import (
	"fmt"
	"math"
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// FormatGB is a size in decimal units: one decimal in GB, whole MB below 1 GB.
// A size above zero never reads as 0 MB.
func FormatGB(gb float64) string {
	if gb < 0 {
		gb = 0
	}
	if gb < 1 {
		mb := math.Round(gb * 1000)
		if mb < 1 && gb > 0 {
			mb = 1
		}
		return fmt.Sprintf("%.0f MB", mb)
	}
	return fmt.Sprintf("%.1f GB", gb)
}

// Wrap breaks text into lines of at most w columns (never fewer than 8), on
// word boundaries where it can and mid-token when a single token (a URL, a
// long command) is longer. Empty text is one empty line.
func Wrap(s string, w int) []string {
	if w < 8 {
		w = 8
	}
	var out []string
	for _, paragraph := range strings.Split(s, "\n") {
		line := ""
		for _, word := range strings.Fields(paragraph) {
			for lipgloss.Width(word) > w {
				if line != "" {
					out = append(out, line)
					line = ""
				}
				runes := []rune(word)
				cut := len(runes)
				for cut > 0 && lipgloss.Width(string(runes[:cut])) > w {
					cut--
				}
				out = append(out, string(runes[:cut]))
				word = string(runes[cut:])
			}
			switch {
			case line == "":
				line = word
			case lipgloss.Width(line)+1+lipgloss.Width(word) <= w:
				line += " " + word
			default:
				out = append(out, line)
				line = word
			}
		}
		if line != "" {
			out = append(out, line)
		}
	}
	if len(out) == 0 {
		return []string{""}
	}
	return out
}
