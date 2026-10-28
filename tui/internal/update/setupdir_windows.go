// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import "golang.org/x/sys/windows/registry"

// setupInstallDir reads the folder gaia-setup.nsi records on install.
func setupInstallDir() string {
	k, err := registry.OpenKey(registry.CURRENT_USER, `Software\GAIA\TerminalHub`, registry.QUERY_VALUE)
	if err != nil {
		return "" // not installed by the setup
	}
	defer k.Close()
	dir, _, err := k.GetStringValue("InstallDir")
	if err != nil {
		return ""
	}
	return dir
}
