// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build linux

package attention

import (
	"fmt"
	"os/exec"
)

// acquireAwake takes a logind idle+sleep inhibitor through systemd-inhibit,
// held by a `cat` reading a pipe only this process writes to: closing the
// pipe releases it, and so does this process dying.
func acquireAwake(lost func(error)) (func() error, error) {
	path, err := exec.LookPath("systemd-inhibit")
	if err != nil {
		return nil, fmt.Errorf("systemd-inhibit was not found (%w)", err)
	}
	cmd := exec.Command(path, // #nosec G204 -- fixed binary and arguments
		"--what=idle:sleep", "--who=GAIA", "--why=GAIA is working on a turn", "--mode=block", "cat")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("could not start systemd-inhibit: %w", err)
	}
	return startHelper(cmd, stdin, lost)
}
