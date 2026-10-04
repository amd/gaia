// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows

package lemonade

// adapterMemoryGB has nothing to read off Windows; Lemonade reports the memory.
func adapterMemoryGB(string) float64 { return 0 }
