// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build windows

package lemonade

import (
	"golang.org/x/sys/windows/registry"
)

// displayClassKey is Windows' display-adapter class; each adapter's subkey
// records its memory.
const displayClassKey = `SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}`

// adapterMemoryGB is the dedicated memory Windows records for the adapter
// name, or 0. Lemonade reports no memory for an integrated AMD GPU on Windows;
// the driver's HardwareInformation.qwMemorySize is its BIOS carve-out, the heap
// llama.cpp's Vulkan backend loads into.
func adapterMemoryGB(name string) float64 {
	if name == "" {
		return 0
	}
	root, err := registry.OpenKey(registry.LOCAL_MACHINE, displayClassKey, registry.ENUMERATE_SUB_KEYS)
	if err != nil {
		return 0
	}
	defer root.Close()
	subkeys, err := root.ReadSubKeyNames(-1)
	if err != nil {
		return 0
	}
	for _, sub := range subkeys {
		adapter, err := registry.OpenKey(root, sub, registry.QUERY_VALUE)
		if err != nil {
			continue // "Properties" and other non-adapter subkeys refuse the read
		}
		desc, _, err := adapter.GetStringValue("DriverDesc")
		if err != nil || desc != name {
			adapter.Close()
			continue
		}
		size, _, err := adapter.GetIntegerValue("HardwareInformation.qwMemorySize")
		adapter.Close()
		if err != nil {
			return 0
		}
		return float64(size) / (1 << 30)
	}
	return 0
}
