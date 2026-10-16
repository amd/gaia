// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows && !darwin && !linux

package attention

import (
	"fmt"
	"runtime"
)

func acquireAwake(_ func(error)) (func() error, error) {
	return nil, fmt.Errorf("GAIA cannot keep the PC awake on %s", runtime.GOOS)
}
