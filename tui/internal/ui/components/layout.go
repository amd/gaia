// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// IndentW is the left margin of the setup screens.
const IndentW = 2

var (
	keyHintStyle  = lipgloss.NewStyle().Bold(true).Foreground(theme.Info)
	keyLabelStyle = lipgloss.NewStyle().Foreground(theme.Dim)
)

// RenderKeys draws a footer's {key, label} pairs joined by " · ".
func RenderKeys(keys [][2]string) string {
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, keyHintStyle.Render(k[0])+" "+keyLabelStyle.Render(k[1]))
	}
	return strings.Join(parts, keyLabelStyle.Render(" · "))
}

// Pad indents one line by the screen margin.
func Pad(s string) string { return strings.Repeat(" ", IndentW) + s }

// IndentAll indents every line by n columns.
func IndentAll(lines []string, n int) []string {
	prefix := strings.Repeat(" ", n)
	out := make([]string, 0, len(lines))
	for _, l := range lines {
		out = append(out, prefix+l)
	}
	return out
}
