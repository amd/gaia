package cards

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// The width a card gets inside an 80-column terminal: chat passes m.width-4.
const width80 = 76

// plain strips styling so assertions compare the text a user reads, not escapes.
func plain(s string) string { return ansi.Strip(s) }

// assertWidth fails unless every rendered line occupies exactly want columns.
// A card that overflows by one column shears every border below it.
func assertWidth(t *testing.T, rendered string, want int) {
	t.Helper()
	for i, line := range strings.Split(plain(rendered), "\n") {
		if got := ansi.StringWidth(line); got != want {
			t.Errorf("line %d width = %d, want %d: %q", i, got, want, line)
		}
	}
}

func assertContains(t *testing.T, rendered string, wants ...string) {
	t.Helper()
	got := plain(rendered)
	for _, w := range wants {
		if !strings.Contains(got, w) {
			t.Errorf("rendered output missing %q\n---\n%s\n---", w, got)
		}
	}
}

func assertNotContains(t *testing.T, rendered string, unwanted ...string) {
	t.Helper()
	got := plain(rendered)
	for _, u := range unwanted {
		if strings.Contains(got, u) {
			t.Errorf("rendered output unexpectedly contains %q\n---\n%s\n---", u, got)
		}
	}
}

func raw(t *testing.T, s string) json.RawMessage {
	t.Helper()
	if !json.Valid([]byte(s)) {
		t.Fatalf("fixture is not valid JSON: %s", s)
	}
	return json.RawMessage(s)
}

func atoi(t *testing.T, s string) int {
	t.Helper()
	n := 0
	for _, r := range strings.TrimSpace(s) {
		if r < '0' || r > '9' {
			t.Fatalf("not a number: %q", s)
		}
		n = n*10 + int(r-'0')
	}
	return n
}
