package status

import (
	"strings"
	"testing"
)

// The zero value of Level must not compare equal to LevelOK: a zero-value
// Outcome{} (e.g. an uninitialised field in a struct literal that forgot to
// set Level) must read as "not OK", never silently pass. See report.go's
// StatePending-at-iota precedent in the preflight package, which this
// mirrors.
func TestLevelZeroValueIsNotOK(t *testing.T) {
	var zero Level
	if zero != LevelUnset {
		t.Fatalf("Level zero value = %v, want LevelUnset", zero)
	}
	if zero == LevelOK {
		t.Fatal("the zero value of Level must not equal LevelOK")
	}
}

// Same invariant for Disposition: an unset disposition on a non-OK row must
// never be mistaken for a deliberate choice to notify-and-proceed.
func TestDispositionZeroValueIsUnset(t *testing.T) {
	var zero Disposition
	if zero != DispositionUnset {
		t.Fatalf("Disposition zero value = %v, want DispositionUnset", zero)
	}
	if zero == DispositionNotify || zero == DispositionHalt {
		t.Fatal("the zero value of Disposition must not equal a real disposition")
	}
}

func TestSanitizeStripsANSIAndC0(t *testing.T) {
	// Captured verbatim from the acceptance criterion: a clear-screen CSI
	// sequence followed by an OSC set-title sequence terminated by BEL.
	const payload = "\x1b[2J\x1b]0;pwned\x07"
	got := Sanitize(payload)
	if strings.ContainsAny(got, "\x1b\x07") {
		t.Fatalf("Sanitize left control bytes in place: %q", got)
	}
	if got != "" {
		t.Fatalf("Sanitize(%q) = %q, want empty — the payload carries no printable text", payload, got)
	}
}

func TestSanitizeStripsBareC0NotJustANSI(t *testing.T) {
	// ansi.Strip on its own leaves a bare, non-escape-sequence control byte
	// (like a lone BEL) in place — Sanitize must go further.
	got := Sanitize("bell\x07here")
	if got != "bellhere" {
		t.Fatalf("Sanitize(%q) = %q, want %q", "bell\x07here", got, "bellhere")
	}
}

func TestSanitizePreservesNewlinesAndTabs(t *testing.T) {
	const s = "line1\nline2\ttabbed"
	if got := Sanitize(s); got != s {
		t.Fatalf("Sanitize(%q) = %q, want it unchanged", s, got)
	}
}

func TestSanitizeLeavesOrdinaryTextAlone(t *testing.T) {
	const s = "the model is loaded with room for about 25037 tokens"
	if got := Sanitize(s); got != s {
		t.Fatalf("Sanitize(%q) = %q, want it unchanged", s, got)
	}
}

// New is the constructor every caller should use, precisely so the
// sanitization above cannot be forgotten at a call site.
func TestNewSanitizesSummary(t *testing.T) {
	o := New("model", "AI model", LevelUnknown, DispositionHalt, "\x1b[2J\x1b]0;pwned\x07 the rest is fine")
	if strings.ContainsAny(o.Summary, "\x1b\x07") {
		t.Fatalf("New did not sanitize Summary: %q", o.Summary)
	}
	if !strings.Contains(o.Summary, "the rest is fine") {
		t.Fatalf("New dropped legitimate text: %q", o.Summary)
	}
	if o.StepID != "model" || o.Label != "AI model" || o.Level != LevelUnknown || o.Disposition != DispositionHalt {
		t.Fatalf("New did not preserve its other fields: %+v", o)
	}
}
