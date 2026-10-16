// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build darwin

package attention

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
)

// acquireAwake runs caffeinate -i (no idle sleep) until released. -w ties it
// to this process, so a crash cannot leave the Mac held awake.
func acquireAwake(lost func(error)) (func() error, error) {
	path, err := exec.LookPath("caffeinate")
	if err != nil {
		return nil, fmt.Errorf("caffeinate was not found (%w)", err)
	}
	cmd := exec.Command(path, "-i", "-w", strconv.Itoa(os.Getpid())) // #nosec G204 -- fixed binary and arguments
	return startHelper(cmd, nil, lost)
}
