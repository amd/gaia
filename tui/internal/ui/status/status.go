// Package status is the shared vocabulary a screen uses to announce how a
// precondition turned out, and what should happen when it is not OK. It is
// NOT a UI event bus — see internal/event, which parses agent SSE payloads
// and is a different thing entirely. Bubble Tea's Update loop is the only
// dispatcher; this package just names one message shape that flows through
// it.
package status

import "github.com/charmbracelet/x/ansi"

// Level is how a precondition turned out. LevelUnset is the zero value and
// deliberately does not compare equal to LevelOK — an Outcome{} nobody
// finished building must read as "not OK", the same way preflight.State
// keeps StatePending off the OK value.
type Level int

const (
	LevelUnset Level = iota
	LevelOK
	// LevelUnknown — probed, but the answer was indeterminate.
	LevelUnknown
	LevelFailed
)

// Disposition is what the checklist does with a row that is not OK. The
// check that produces the row declares it, because the check is what knows
// whether being unverified will actually bite the user.
type Disposition int

const (
	// DispositionUnset means nobody decided. A non-OK row must never carry
	// this — see the exhaustiveness test in the preflight package.
	DispositionUnset Disposition = iota
	// DispositionNotify — name it on screen, expire, move on.
	DispositionNotify
	// DispositionHalt — hold the screen until a person decides.
	DispositionHalt
)

// Outcome is how one precondition turned out.
type Outcome struct {
	// StepID is the stable identifier of the check that produced this
	// outcome, e.g. "model" — it keys de-dup and session suppression.
	StepID string
	// Label is the human name, e.g. "AI model".
	Label string
	Level Level
	// Disposition is what a listener should do about a non-OK Level.
	Disposition Disposition
	// Summary is one sanitized line of why. Build it through New, or through
	// Sanitize directly, rather than assigning it — the text this carries
	// usually originated on an upstream server the terminal must not trust.
	Summary string
}

// New builds an Outcome, sanitizing summary. Upstream text — a probe's raw
// hint, an upstream diagnosis — reaches this package unescaped; this is the
// one place on the path to the terminal that must not assume it is inert.
func New(stepID, label string, level Level, disposition Disposition, summary string) Outcome {
	return Outcome{
		StepID:      stepID,
		Label:       label,
		Level:       level,
		Disposition: disposition,
		Summary:     Sanitize(summary),
	}
}

// Sanitize strips ANSI escape sequences and bare control bytes (other than
// \n and \t) from s. ansi.Strip alone removes escape SEQUENCES — a
// clear-screen CSI, an OSC set-title — but leaves a lone control byte, such
// as a stray BEL, a raw DEL, or a valid UTF-8 encoding of a C1 control
// (U+0080-U+009F, e.g. a single-byte-CSI trigger), sitting in the string, so
// this does a second pass over what is left. Each of those was verified to
// survive ansi.Strip before this function was written to catch it, rather
// than assumed.
func Sanitize(s string) string {
	s = ansi.Strip(s)
	out := make([]rune, 0, len(s))
	for _, r := range s {
		switch {
		case r == '\n' || r == '\t':
			out = append(out, r)
		case r < 0x20, r == 0x7f, r >= 0x80 && r <= 0x9f:
			// C0, DEL, and C1 control ranges.
			continue
		default:
			out = append(out, r)
		}
	}
	return string(out)
}
