// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build !windows && !darwin && !linux

package chat

// readClipboardImagePNG has no implementation on this platform — ok is
// always false, so Ctrl+V falls straight through to the existing text-only
// clipboard.ReadAll() path (pasteFromClipboardOrImage) rather than the
// key doing nothing at all.
func readClipboardImagePNG() (data []byte, ok bool, err error) {
	return nil, false, nil
}
