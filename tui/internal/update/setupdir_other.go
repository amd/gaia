// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package update

// setupInstallDir: the Windows setup does not exist on this OS.
func setupInstallDir() string { return "" }
