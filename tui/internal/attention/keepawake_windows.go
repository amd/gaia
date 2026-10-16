// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build windows

package attention

import (
	"fmt"

	"golang.org/x/sys/windows"
)

const (
	esContinuous     = 0x80000000
	esSystemRequired = 0x00000001
)

var procSetThreadExecutionState = windows.NewLazySystemDLL("kernel32.dll").NewProc("SetThreadExecutionState")

// acquireAwake keeps the system (not the display) awake until released. It
// must be released from the same OS thread, which Keeper guarantees. Nothing
// but the release ends it, so lost is never called.
func acquireAwake(_ func(error)) (func() error, error) {
	if err := setExecutionState(esContinuous | esSystemRequired); err != nil {
		return nil, err
	}
	return func() error { return setExecutionState(esContinuous) }, nil
}

func setExecutionState(flags uint32) error {
	if err := procSetThreadExecutionState.Find(); err != nil {
		return fmt.Errorf("Windows has no SetThreadExecutionState (%w)", err)
	}
	prev, _, callErr := procSetThreadExecutionState.Call(uintptr(flags))
	if prev == 0 {
		return fmt.Errorf("Windows refused SetThreadExecutionState (%v)", callErr)
	}
	return nil
}
