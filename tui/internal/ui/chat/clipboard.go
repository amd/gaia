// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/atotto/clipboard"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// The whole point of a terminal answer is to use it somewhere else — paste the
// command, keep the summary, mail the list. In an alt-screen app the terminal's
// own selection is fighting the mouse-motion reporting the transcript needs to
// scroll, so "just select it" is exactly the thing that does not work here.
//
// OSC 52 asks the TERMINAL to set the clipboard, which is what makes this work
// unchanged over SSH — no cgo, no X11 socket, no pbcopy/xclip/wl-copy to shell
// out to and no platform matrix to keep alive. The cost is that support is not
// universal and, by design, nothing reports back: the escape is write-only, so
// a terminal that ignores it does so silently. Hence copyHint below, which
// promises only what was attempted.
//
// Known non-support at time of writing: Windows Console Host (conhost) and the
// older mintty builds. Windows Terminal, iTerm2, kitty, WezTerm, Alacritty,
// foot and tmux (with set-clipboard on) all handle it.
type clipboardResultMsg struct {
	label string
	err   error
}

// copyToClipboard emits the OSC 52 sequence on stdout.
//
// Writing straight to os.Stdout under Bubble Tea is safe ONLY because this
// sequence draws nothing and moves nothing: it sets a terminal property and
// emits no cells, so it cannot corrupt the frame Bubble Tea is composing. Do
// not copy this pattern for anything that renders — that has to go through the
// model's View.
func copyToClipboard(text, label string) tea.Cmd {
	return func() tea.Msg {
		if strings.TrimSpace(text) == "" {
			return clipboardResultMsg{label: label, err: fmt.Errorf("nothing to copy")}
		}
		if _, err := os.Stdout.WriteString(ansi.SetClipboard(ansi.SystemClipboard, text)); err != nil {
			return clipboardResultMsg{label: label, err: err}
		}
		return clipboardResultMsg{label: label}
	}
}

// lastAnswer is the most recent thing the agent said, in its source form —
// the markdown, not the ANSI-rendered copy. Pasting escape codes into an
// editor is never what anyone meant by "copy the answer".
func (m ChatModel) lastAnswer() string {
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleAssistant {
			return m.messages[i].Content
		}
	}
	return ""
}

// lastCodeBlock is the last fenced block in the most recent answer — the thing
// a person copies far more often than the prose around it.
//
// Deliberately a scan for ``` rather than a markdown parse: the answer is
// already known-good markdown, and the failure mode of a parser here (drop the
// block, copy nothing) is worse than the failure mode of the scan (copy one
// block too many). An unterminated fence — a streamed answer cut off
// mid-block — yields everything after it, which is the useful reading.
func lastCodeBlock(markdown string) string {
	lines := strings.Split(markdown, "\n")

	end := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if !strings.HasPrefix(strings.TrimSpace(lines[i]), "```") {
			continue
		}
		if end < 0 {
			end = i
			continue
		}
		return strings.Join(lines[i+1:end], "\n")
	}
	if end >= 0 {
		// A lone fence is an OPENING one whose answer was cut off mid-block —
		// the common case while a reply is still streaming. What follows it is
		// the code; what precedes it is the prose that introduced it.
		return strings.Join(lines[end+1:], "\n")
	}
	return ""
}

// copyHint is what the status line says afterwards. It claims the copy was
// SENT, never that it landed: OSC 52 has no reply, so a terminal that drops it
// leaves us no way to know, and "copied!" over an empty clipboard is worse than
// a hedged sentence.
func copyHint(label string, err error) string {
	if err != nil {
		return fmt.Sprintf("could not copy the %s: %v", label, err)
	}
	return fmt.Sprintf("%s sent to the clipboard — if nothing pasted, this terminal does not support OSC 52", label)
}

// pasteClipboardMsg carries the result of a Ctrl+V clipboard read.
type pasteClipboardMsg struct {
	text string
	err  error
}

// pasteFromClipboard reads the OS clipboard directly, for the terminals that
// do not turn Ctrl+V into a bracketed paste themselves (handleKey's Paste
// branch covers the ones that do).
//
// atotto/clipboard was already pulled in transitively — bubbles/textarea
// binds its own Ctrl+V to it — so this adds nothing to the dependency tree.
// It shells out on Linux/macOS (xclip/xsel, pbpaste) but calls the Win32
// clipboard API directly on Windows, no subprocess. OSC 52 read was
// considered and rejected: terminal support for the read direction is far
// rarer than write, and many terminals refuse it outright for security, which
// would make Ctrl+V a coin flip instead of a reliable key.
//
// bubbles/textarea has this exact binding built in already, but its result
// lands in an m.Err field ChatModel never reads — a failed read is a true
// silent no-op there. Reading it here instead keeps the failure visible.
func pasteFromClipboard() tea.Cmd {
	return func() tea.Msg {
		text, err := clipboard.ReadAll()
		return pasteClipboardMsg{text: text, err: err}
	}
}

// pasteImageMsg carries the result of a Ctrl+V clipboard read that found an
// IMAGE rather than text — a screenshot (Win+Shift+S, Cmd+Shift+4, a Linux
// screenshot tool) on the clipboard, written out to a temp file so the agent
// can be handed a path to it, the same way a dropped file already is.
type pasteImageMsg struct {
	path string
	err  error
}

// pasteFromClipboardOrImage is Ctrl+V's real entry point. Which slot wins
// when the clipboard holds both text and an image depends on what the copy
// MEANT:
//
//   - a screenshot tool (Win+Shift+S, Cmd+Shift+4) puts only an image → image
//   - Excel/Word cells put the cells as text plus a bitmap rendering of
//     them → the text is the copy, image-first pasted a picture of it
//   - a browser's "Copy image" puts the bitmap plus its source URL as
//     text → the image is the copy, text-first pasted a URL
//
// So: substantial text wins; a lone URL alongside an image is treated as the
// image's caption, not the payload.
//
// readClipboardImagePNG (one implementation per OS — clipboardimage_*.go) is
// the only OS-specific part; everything after it (encode-to-temp-file,
// insert-the-path) is shared, so a platform that cannot read clipboard images
// at all (clipboardimage_other.go) still gets a correct, if narrower, Ctrl+V.
func pasteFromClipboardOrImage() tea.Cmd {
	return func() tea.Msg {
		text, terr := clipboard.ReadAll()
		hasText := terr == nil && strings.TrimSpace(text) != ""
		if hasText && !looksLikeImageCaption(text) {
			return pasteClipboardMsg{text: text, err: nil}
		}
		png, ok, err := readClipboardImagePNG()
		if ok {
			// The image is the payload here. A decode failure surfaces —
			// silently pasting the caption URL instead would hide that the
			// image copy failed (the platform readers' documented contract).
			if err != nil {
				return pasteImageMsg{err: err}
			}
			path, werr := writeClipboardImageToTemp(png)
			return pasteImageMsg{path: path, err: werr}
		}
		if hasText {
			// No image at all — the caption-ish text is all there is.
			return pasteClipboardMsg{text: text, err: nil}
		}
		return pasteClipboardMsg{text: text, err: terr}
	}
}

// writeClipboardImageToTemp saves PNG bytes the clipboard held to a fresh
// file under the OS temp directory, so it can be handed to the agent as a
// path — the same shape a dropped file already arrives in. Kept separate
// from readClipboardImagePNG (which is OS-specific and not exercised by a
// unit test — there is no way to put a real image on the clipboard in CI) so
// this half, the part actually worth testing, is a pure function of bytes in,
// a file on disk out.
func writeClipboardImageToTemp(png []byte) (string, error) {
	// Under the user's HOME, not os.TempDir(): the gaia agent's default file
	// sandbox is the home directory, and on Linux/macOS the system temp dir
	// lives outside it — the agent would refuse the very path this paste
	// inserts. A dedicated subdirectory, swept on use, keeps pastes from
	// accumulating forever.
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("could not resolve a home directory for the pasted image: %w", err)
	}
	dir := filepath.Join(home, ".gaia", "paste")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("could not create the paste directory %s: %w", dir, err)
	}
	sweepOldPastes(dir, 7*24*time.Hour)
	f, err := os.CreateTemp(dir, "gaia-paste-*.png")
	if err != nil {
		return "", fmt.Errorf("could not create a temp file for the pasted image: %w", err)
	}
	defer f.Close()
	if _, err := f.Write(png); err != nil {
		return "", fmt.Errorf("could not write the pasted image to %s: %w", f.Name(), err)
	}
	return f.Name(), nil
}

// looksLikeImageCaption reports whether clipboard text reads as the metadata
// a "Copy image" action leaves beside the bitmap — a single URL (or data:
// URI) — rather than content someone copied for its own sake.
func looksLikeImageCaption(text string) bool {
	t := strings.TrimSpace(text)
	if t == "" || strings.ContainsAny(t, "\n\t") {
		return false
	}
	return strings.HasPrefix(t, "http://") ||
		strings.HasPrefix(t, "https://") ||
		strings.HasPrefix(t, "data:image/") ||
		strings.HasPrefix(t, "file://")
}

// sweepOldPastes deletes pasted images older than maxAge from dir. Best-effort
// housekeeping on the paste path: a file that cannot be listed or removed is
// left for the next sweep rather than failing the paste the user is making.
func sweepOldPastes(dir string, maxAge time.Duration) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	cutoff := time.Now().Add(-maxAge)
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), "gaia-paste-") {
			continue
		}
		info, err := e.Info()
		if err != nil || info.IsDir() {
			continue
		}
		if info.ModTime().Before(cutoff) {
			_ = os.Remove(filepath.Join(dir, e.Name()))
		}
	}
}

// pasteImageHint is what the status line says once a pasted screenshot has
// been written out — the path alone in the composer doesn't say WHY it's
// there, or how big the file is, for a person who just wanted to check it
// grabbed the right one.
func pasteImageHint(path string, size int, err error) string {
	if err != nil {
		return fmt.Sprintf("could not paste the clipboard image: %v", err)
	}
	return fmt.Sprintf("pasted image saved to %s (%s)", path, formatByteSize(size))
}

func formatByteSize(n int) string {
	if n < 1024 {
		return fmt.Sprintf("%d B", n)
	}
	if n < 1024*1024 {
		return fmt.Sprintf("%.1f KB", float64(n)/1024)
	}
	return fmt.Sprintf("%.1f MB", float64(n)/(1024*1024))
}

// quotePathForComposer wraps path in double quotes when it contains
// whitespace, matching how Windows Terminal already quotes a dropped file's
// path before it ever reaches the composer (see docs/guides/terminal-hub.mdx)
// — a pasted screenshot's temp path must parse the same way a dropped one
// does, and %TEMP% commonly contains a space (the Windows profile directory
// name).
func quotePathForComposer(path string) string {
	if strings.ContainsAny(path, " \t") {
		return `"` + path + `"`
	}
	return path
}

// pasteHint is what the status line says when Ctrl+V put nothing in the
// composer — silence there would read as the key doing nothing at all.
func pasteHint(err error) string {
	if err != nil {
		return fmt.Sprintf("could not read the clipboard: %v — try Ctrl+T to paste with your terminal's own paste instead", err)
	}
	return "clipboard is empty — nothing to paste"
}

// normalizePastedText collapses CRLF and lone CR to LF before pasted text
// reaches the composer. The textarea's sanitizer treats \r and \n as two
// independent newlines, so untouched Windows line endings (clipboard text
// and some terminals' bracketed paste both use \r\n) would double every line
// break into a blank line.
func normalizePastedText(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n")
}
