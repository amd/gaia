package preflight

import (
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Remedies are written for the interactive gate, where a key re-checks or
// fixes. Report.String serves callers with no keys to press — a one-shot that
// has already exited — so it renders each remedy through headlessAction.

var (
	// keystroke matches any instruction to press one of the gate's keys.
	keystroke = regexp.MustCompile(`(?i)\bpress (?:[a-z]|enter|esc)\b`)
	// keystrokeClause is a trailing "…, then press r to re-check" style clause.
	// It stops at a dash or semicolon so a reason after it survives.
	keystrokeClause = regexp.MustCompile(`(?i)(?:\s*[,;]|\s*—)?\s*(?:then |so |and )?press (?:[a-z]|enter|esc)\b[^.;—]*`)
	// leadingKeystroke is a sentence that opens on the key.
	leadingKeystroke = regexp.MustCompile(`(?i)^(?:then |or )?press (?:[a-z]|enter|esc)\b`)
	// pressDash is "Press r — <reason>": the reason stands on its own.
	pressDash   = regexp.MustCompile(`(?i)^(?:then )?press (?:[a-z]|enter|esc)\s+—\s+(.+)$`)
	dashSpacing = regexp.MustCompile(`\s*—\s*`)
	// anotherKey is the "g to …" in "Press f to use it, or g to …".
	anotherKey = regexp.MustCompile(`^[a-z] to\b`)
)

// headlessAction rewrites an interactive remedy for a reader with no keys:
// keystroke clauses are cut, a sentence that is only a keystroke is dropped,
// and "Press f to X, or Y" keeps Y.
func headlessAction(action string) string {
	var kept []string
	for _, sentence := range splitSentences(action) {
		if s := headlessSentence(sentence); s != "" {
			kept = append(kept, s)
		}
	}
	return strings.Join(kept, " ")
}

func headlessSentence(sentence string) string {
	if !keystroke.MatchString(sentence) {
		return sentence
	}
	end := ""
	if n := len(sentence); n > 0 && strings.ContainsRune(".!?", rune(sentence[n-1])) {
		end = sentence[n-1:]
		sentence = sentence[:n-1]
	}

	if m := pressDash.FindStringSubmatch(sentence); m != nil {
		sentence = capitalize(m[1])
	} else if leadingKeystroke.MatchString(sentence) {
		_, rest, ok := strings.Cut(sentence, ", or ")
		if !ok || anotherKey.MatchString(rest) {
			return ""
		}
		sentence = capitalize(rest)
	}

	sentence = keystrokeClause.ReplaceAllString(sentence, "")
	sentence = dashSpacing.ReplaceAllString(sentence, " — ")
	sentence = strings.TrimSpace(strings.TrimRight(sentence, " ,;:—"))
	// Anything still asking for a key cannot be followed here.
	if sentence == "" || keystroke.MatchString(sentence) {
		return ""
	}
	if end == "" {
		end = "."
	}
	return sentence + end
}

// splitSentences splits after ". ", "! " or "? ", keeping the punctuation.
func splitSentences(s string) []string {
	var out []string
	start := 0
	for i := 0; i+1 < len(s); i++ {
		if strings.ContainsRune(".!?", rune(s[i])) && s[i+1] == ' ' {
			out = append(out, strings.TrimSpace(s[start:i+1]))
			start = i + 2
		}
	}
	if rest := strings.TrimSpace(s[start:]); rest != "" {
		out = append(out, rest)
	}
	return out
}

func capitalize(s string) string {
	r, n := utf8.DecodeRuneInString(s)
	if r == utf8.RuneError {
		return s
	}
	return string(unicode.ToUpper(r)) + s[n:]
}
