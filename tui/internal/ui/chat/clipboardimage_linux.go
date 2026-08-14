// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"os/exec"
)

// linuxImageClipboardTools are tried in order — xclip covers X11, wl-paste
// covers Wayland, and neither is guaranteed installed, so a missing binary
// (exec.Command returning an error) just moves on to the next rather than
// failing the whole read.
var linuxImageClipboardTools = []struct {
	name string
	args []string
}{
	{"wl-paste", []string{"--type", "image/png", "--no-newline"}},
	{"xclip", []string{"-selection", "clipboard", "-t", "image/png", "-o"}},
}

// readClipboardImagePNG shells out to whichever clipboard tool this desktop
// actually has — there is no cgo-free, tool-free way to read clipboard image
// bytes on Linux the way clipboard.go's text path calls the Win32 API
// directly on Windows; a clipboard tool IS the platform's answer here (the
// same reasoning atotto/clipboard's own Linux build already relies on for
// text, via xclip/xsel).
func readClipboardImagePNG() (data []byte, ok bool, err error) {
	for _, tool := range linuxImageClipboardTools {
		out, runErr := exec.Command(tool.name, tool.args...).Output()
		if runErr != nil {
			// Not installed, or nothing image/png-shaped on the clipboard
			// (xclip/wl-paste both exit non-zero for "no such target") —
			// either way, try the next tool rather than surfacing an error
			// for what is very often just "the clipboard holds text".
			continue
		}
		if len(bytes.TrimSpace(out)) == 0 {
			continue
		}
		return out, true, nil
	}
	return nil, false, nil
}
