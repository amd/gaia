package chat

import (
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// sanitizeErrorText makes a tool-supplied string safe for the RoleError sink.
//
// Unlike the card path (box.add -> cards.clean()), model.go's RoleError case
// renders "[!] " + msg.Content through a bare lipgloss style with no
// scrubbing of its own — so a tool error reaching Message.Content unsanitized
// can carry a live ANSI escape or control byte onto the terminal.
// cards.clean() cannot be reused as-is: it maps newlines to spaces, which
// would flatten the sidecar's `gaia connectors connect ...` remedy onto one
// line — exactly the text this fix exists to keep readable. This helper
// differs from cards.clean() only there: newlines survive, tabs still become
// a space exactly as cards.clean() does ("keep the word break, drop the
// cursor movement"), and \r is still dropped so \r\n collapses to \n rather
// than leaving a trailing space.
func sanitizeErrorText(s string) string {
	if s == "" {
		return s
	}
	if strings.ContainsRune(s, 0x1b) {
		s = ansi.Strip(s)
	}
	return strings.Map(func(r rune) rune {
		switch {
		case r == '\n':
			return r
		case r == '\t':
			return ' '
		case r < 0x20 || r == 0x7f:
			return -1
		}
		return r
	}, s)
}
