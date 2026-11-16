// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package textutil

import "testing"

func TestFormatGB(t *testing.T) {
	for gb, want := range map[float64]string{
		23.3:   "23.3 GB",
		1:      "1.0 GB",
		63.6:   "63.6 GB",
		0.33:   "330 MB",
		0.5:    "500 MB",
		0.0001: "1 MB",
		0:      "0 MB",
		-2:     "0 MB",
	} {
		if got := FormatGB(gb); got != want {
			t.Errorf("FormatGB(%v) = %q, want %q", gb, got, want)
		}
	}
}
