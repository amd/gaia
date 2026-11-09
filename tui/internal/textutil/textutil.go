// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package textutil holds plain-text formatting shared by the UI and the
// packages under it, so one value never prints two ways.
package textutil

import (
	"fmt"
	"math"
	"strings"
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

// Clip shortens s to at most n runes, ending in "…". It cuts on runes because
// byte-slicing splits multibyte characters into mojibake.
func Clip(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	if n < 1 {
		return ""
	}
	return strings.TrimSpace(string(r[:n-1])) + "…"
}
