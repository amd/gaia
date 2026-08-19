// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
)

// readClipboardImagePNG asks AppleScript to coerce the clipboard to a PNG
// («class PNGf») and write it to a temp file, rather than shelling out to a
// third-party helper (pngpaste) this build cannot assume is installed —
// osascript ships with every macOS install, and writing to a file sidesteps
// piping raw image bytes through osascript's own stdout, which mangles
// anything that isn't text. AppleScript itself is the check for "is there an
// image on the clipboard at all": the coercion fails when the clipboard
// holds text instead, which the script's own `try` turns into an empty
// path — exactly the false/false ("no image, try text") this function needs
// to report, no separate probe required.
func readClipboardImagePNG() (data []byte, ok bool, err error) {
	png, probeErr := macClipboardPNGViaTempFile()
	if probeErr != nil {
		// Cannot tell "no image on the clipboard" apart from "osascript
		// itself failed" from the exit code alone — treat either as "no
		// image", so a real text copy still falls through to the normal
		// paste path instead of surfacing an AppleScript error for the
		// common case (nothing image-shaped on the clipboard at all).
		return nil, false, nil
	}
	return png, true, nil
}

// macClipboardPNGViaTempFile is separated out so the exec.Command
// construction (the part worth reading) isn't buried in string-escaping.
func macClipboardPNGViaTempFile() ([]byte, error) {
	script := `set tmpFile to (POSIX path of (path to temporary items)) & "gaia-clip-check.png"
try
	set theData to the clipboard as «class PNGf»
on error
	return ""
end try
set fh to open for access POSIX file tmpFile with write permission
set eof fh to 0
write theData to fh
close access fh
return tmpFile`

	out, err := exec.Command("osascript", "-e", script).Output()
	if err != nil {
		return nil, fmt.Errorf("osascript: %w", err)
	}
	path := string(bytes.TrimSpace(out))
	if path == "" {
		return nil, fmt.Errorf("no image on the clipboard")
	}
	defer os.Remove(path)
	return os.ReadFile(path)
}
