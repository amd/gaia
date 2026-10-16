// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build windows

package attention

import "golang.org/x/sys/windows"

// ReportsFocus is false on Windows: Bubble Tea reads console input records
// there and drops FOCUS_EVENT, so CSI ?1004h would never reach the program.
// The foreground window is asked instead.
const ReportsFocus = false

var (
	user32                  = windows.NewLazySystemDLL("user32.dll")
	procGetForegroundWindow = user32.NewProc("GetForegroundWindow")
	procGetAncestor         = user32.NewProc("GetAncestor")
	procIsWindowVisible     = user32.NewProc("IsWindowVisible")
	procGetConsoleWindow    = windows.NewLazySystemDLL("kernel32.dll").NewProc("GetConsoleWindow")
)

const gaRootOwner = 3

// ForegroundProbe reports whether this console's window is the one in front.
//
// Under Windows Terminal the console window is a hidden stand-in owned by the
// terminal's real window, so its root owner is what gets compared. A hidden
// window with no owner (an old terminal, a headless host) cannot be placed on
// screen, and the probe says it does not know.
//
// It answers per window, not per tab: another tab of the same Windows
// Terminal window counts as in front.
func ForegroundProbe() func() (focused, known bool) {
	for _, p := range []*windows.LazyProc{procGetForegroundWindow, procGetAncestor, procIsWindowVisible, procGetConsoleWindow} {
		if p.Find() != nil {
			return nil
		}
	}
	return func() (bool, bool) {
		con, _, _ := procGetConsoleWindow.Call()
		if con == 0 {
			return false, false
		}
		owner, _, _ := procGetAncestor.Call(con, gaRootOwner)
		if owner == 0 || owner == con {
			if visible, _, _ := procIsWindowVisible.Call(con); visible == 0 {
				return false, false
			}
			owner = con
		}
		fg, _, _ := procGetForegroundWindow.Call()
		return fg == owner || fg == con, true
	}
}
