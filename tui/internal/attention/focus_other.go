// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package attention

// ReportsFocus is true where the terminal's focus reports (CSI ?1004h, then
// CSI I / CSI O) reach the program as Bubble Tea FocusMsg / BlurMsg.
const ReportsFocus = true

// ForegroundProbe is nil: focus reports are the source here.
func ForegroundProbe() func() (focused, known bool) { return nil }
