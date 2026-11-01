// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"fmt"
	"math"
	"path/filepath"
	"strings"
	"time"
)

// Decimal units throughout: 1 GB = 1000 MB = 10^9 bytes, the unit the backend
// and model_fit use, so "needs ~27 GB" and the GPU split compare directly.

// formatGB is a download size: one decimal, MB below 1 GB.
func formatGB(gb float64) string {
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

// formatGBShort is a memory size: whole GB from 10 up, one decimal below, and
// never a trailing ".0".
func formatGBShort(gb float64) string {
	if gb >= 10 {
		return fmt.Sprintf("%.0f GB", math.Round(gb))
	}
	s := fmt.Sprintf("%.1f", gb)
	return strings.TrimSuffix(s, ".0") + " GB"
}

// formatGiB is memory Lemonade reports in GiB, labelled as such.
func formatGiB(gib float64) string {
	return strings.TrimSuffix(formatGBShort(gib), " GB") + " GiB"
}

func bytesGB(b int64) float64 { return float64(b) / 1e9 }

// formatSpeed is a download rate.
func formatSpeed(bps float64) string {
	switch {
	case bps >= 1e9:
		return fmt.Sprintf("%.1f GB/s", bps/1e9)
	case bps >= 1e6:
		return fmt.Sprintf("%.0f MB/s", bps/1e6)
	case bps >= 1e3:
		return fmt.Sprintf("%.0f KB/s", bps/1e3)
	}
	return fmt.Sprintf("%.0f B/s", bps)
}

// formatETA is how long is left, rounded the way a person says it: seconds
// under 90 s, minutes after that, and no number at all past two hours.
func formatETA(d time.Duration) string {
	s := d.Seconds()
	switch {
	case s < 90:
		n := int(math.Ceil(s))
		if n < 1 {
			n = 1
		}
		return fmt.Sprintf("about %d sec", n)
	case s > 2*3600:
		return "over 2 hours"
	}
	return fmt.Sprintf("about %d min", int(math.Round(s/60)))
}

// formatElapsed is m:ss, for a step that has run a while.
func formatElapsed(d time.Duration) string {
	d = d.Round(time.Second)
	return fmt.Sprintf("%d:%02d", int(d.Minutes()), int(d.Seconds())%60)
}

// shortName is a model id as people read it: no "user." prefix, no "-GGUF".
func shortName(id string) string {
	name := strings.TrimPrefix(id, "user.")
	for _, suffix := range []string{"-GGUF", "-gguf"} {
		name = strings.TrimSuffix(name, suffix)
	}
	return name
}

// driveOf names the drive a path is on: "C:" on Windows, and
// "disk" elsewhere, where a mount path is too long for the totals line.
func driveOf(path *string) string {
	if path == nil || strings.TrimSpace(*path) == "" {
		return "the model drive"
	}
	p := *path
	if len(p) >= 2 && p[1] == ':' {
		return strings.ToUpper(p[:2])
	}
	if v := filepath.VolumeName(p); v != "" {
		return v
	}
	return "disk"
}

// sameID matches model ids the way the backend does: ignoring the "user."
// prefix and letter case.
func sameID(a, b string) bool {
	return strings.EqualFold(strings.TrimPrefix(a, "user."), strings.TrimPrefix(b, "user."))
}
