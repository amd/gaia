// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package speech

import "os/exec"

func hideWindow(*exec.Cmd) {}
