package client

import (
	"fmt"
	"strings"
)

// SplitCommandLine splits a user-supplied command string into argv, honouring
// single and double quotes and backslash escapes.
//
// Whitespace splitting alone (strings.Fields) silently corrupts any path with a
// space in it — "/Users/me/My Agents/gaia-bash" becomes two argv entries and the
// launch fails with a confusing "no such file". Unbalanced quoting is an error,
// not a guess.
func SplitCommandLine(cmdLine string) ([]string, error) {
	var (
		argv    []string
		cur     strings.Builder
		inWord  bool
		quote   rune // 0, '\'' or '"'
		escaped bool
	)

	flush := func() {
		if inWord {
			argv = append(argv, cur.String())
			cur.Reset()
			inWord = false
		}
	}

	for _, r := range cmdLine {
		switch {
		case escaped:
			cur.WriteRune(r)
			inWord = true
			escaped = false
		case quote == '\'':
			// Single quotes are literal — not even a backslash escapes inside them.
			if r == '\'' {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
		case quote == '"':
			switch r {
			case '"':
				quote = 0
			case '\\':
				escaped = true
			default:
				cur.WriteRune(r)
			}
		case r == '\\':
			escaped = true
		case r == '\'' || r == '"':
			quote = r
			inWord = true
		case r == ' ' || r == '\t' || r == '\n' || r == '\r':
			flush()
		default:
			cur.WriteRune(r)
			inWord = true
		}
	}

	if escaped {
		return nil, fmt.Errorf("command %q ends with a dangling backslash", cmdLine)
	}
	if quote != 0 {
		return nil, fmt.Errorf("command %q has an unbalanced %c quote", cmdLine, quote)
	}
	flush()

	if len(argv) == 0 {
		return nil, fmt.Errorf("command %q contains no executable to run", cmdLine)
	}
	return argv, nil
}
