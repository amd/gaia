package dedent

import (
	"bytes"
)

// String automatically detects the maximum indentation shared by all lines and
// trims them accordingly.
func String(s string) string {
	indent := minIndent(s)
	if indent == 0 {
		return s
	}

	return dedent(s, indent)
}

func minIndent(s string) int {
	var (
		curIndent    int
		minIndent    int
		shouldAppend = true
	)
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case ' ', '\t':
			if shouldAppend {
				curIndent++
			}
		case '\n':
			curIndent = 0
			shouldAppend = true
		default:
			if curIndent > 0 && (minIndent == 0 || curIndent < minIndent) {
				minIndent = curIndent
				curIndent = 0
			}
			shouldAppend = false
		}
	}

	return minIndent
}

func dedent(s string, indent int) string {
	var (
		omitted    int
		shouldOmit = true
		buf        bytes.Buffer
	)

	for i := 0; i < len(s); i++ {
		switch s[i] {
		case ' ', '\t':
			if shouldOmit {
				if omitted < indent {
					omitted++
					continue
				}
				shouldOmit = false
			}
			_ = buf.WriteByte(s[i])
		case '\n':
			omitted = 0
			shouldOmit = true
			_ = buf.WriteByte(s[i])
		default:
			_ = buf.WriteByte(s[i])
		}
	}

	return buf.String()
}
