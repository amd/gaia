package cards

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// Card `data` is agent-supplied and reaches the terminal. Anything in it that
// carries width — control characters, ANSI escapes, wide runes — must not be
// able to shear a border, hang the render, or reach the terminal as a control
// sequence. Values are plain text per contract §4.3; they are not a styling
// channel.
var hostileStrings = map[string]string{
	"newline":          "hel\nlo",
	"crlf":             "hel\r\nlo",
	"carriage return":  "hel\rlo",
	"tab":              "hel\tlo",
	"ansi colour":      "\x1b[31mred\x1b[0m",
	"ansi no reset":    "\x1b[31m" + strings.Repeat("A", 40),
	"ansi with reset":  "\x1b[31m" + strings.Repeat("A", 40) + "\x1b[0m",
	"ansi mid word":    strings.Repeat("A", 10) + "\x1b[31m" + strings.Repeat("B", 30),
	"cursor move":      "\x1b[2J\x1b[Hcleared",
	"osc title":        "\x1b]0;pwned\x07text",
	"bel":              "ding\x07dong",
	"backspace":        "over\x08write",
	"nul":              "nul\x00byte",
	"del":              "del\x7fbyte",
	"cjk":              strings.Repeat("字", 60),
	"combining":        strings.Repeat("é", 60),
	"zwj emoji":        strings.Repeat("\U0001F469‍\U0001F4BB", 20),
	"400 char word":    strings.Repeat("A", 400),
	"only whitespace":  "   \t\n  ",
	"empty":            "",
	"mixed everything": "a\nb\tc\x1b[31md\x1b[0m字\U0001F469‍\U0001F4BB" + strings.Repeat("z", 100),
}

// renderTimeout guards against a wrap/layout loop that never terminates. A hang
// here is worse than a blank card: it freezes the Bubble Tea update goroutine
// and the whole terminal with it.
func renderWithin(t *testing.T, d time.Duration, fn func() string) string {
	t.Helper()
	done := make(chan string, 1)
	go func() {
		defer func() {
			if r := recover(); r != nil {
				done <- "PANIC: " + toString(r)
			}
		}()
		done <- fn()
	}()
	select {
	case out := <-done:
		if strings.HasPrefix(out, "PANIC: ") {
			t.Fatal(out)
		}
		return out
	case <-time.After(d):
		t.Fatal("render did not return — the layout loop does not terminate")
		return ""
	}
}

func toString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	if e, ok := v.(error); ok {
		return e.Error()
	}
	return "non-string panic value"
}

// The invariant that matters, over every card type, every hostile string and a
// range of widths: the render terminates, every line is exactly the requested
// width, and no control character survives into the output.
func TestEveryCardSurvivesHostileStrings(t *testing.T) {
	for name, s := range hostileStrings {
		for _, payload := range hostilePayloads(t, s) {
			for _, w := range []int{24, 40, 80} {
				out := renderWithin(t, 5*time.Second, func() string {
					return Render(payload.key, payload.data, w)
				})

				label := name + "/" + payload.key + "/w" + itoa(w)
				if strings.TrimSpace(out) == "" {
					t.Errorf("%s: rendered nothing", label)
					continue
				}
				for i, line := range strings.Split(plain(out), "\n") {
					if got := visualLen(line); got != w {
						t.Errorf("%s: line %d width = %d, want %d: %q", label, i, got, w, line)
					}
				}
				assertNoControlChars(t, label, out)
			}
		}
	}
}

// assertNoControlChars fails if a control character reached the output. The
// cards package emits no styling of its own, so anything here came from the
// agent's payload and would be interpreted by the terminal.
func assertNoControlChars(t *testing.T, label, rendered string) {
	t.Helper()
	for _, r := range rendered {
		if r == '\n' {
			continue // the card's own line separator
		}
		if r < 0x20 || r == 0x7f {
			t.Errorf("%s: control character %q survived into the rendered card", label, r)
			return
		}
	}
}

type hostilePayload struct {
	key  string
	data json.RawMessage
}

func hostilePayloads(t *testing.T, s string) []hostilePayload {
	t.Helper()
	q := quote(s)
	return []hostilePayload{
		{"table", json.RawMessage(`{"title":` + q + `,"columns":["a",` + q + `],"rows":[[` + q + `,"x"]]}`)},
		{"key_value", json.RawMessage(`{"title":` + q + `,"items":[{"key":` + q + `,"value":` + q + `}]}`)},
		{"list", json.RawMessage(`{"title":` + q + `,"items":[` + q + `,"plain"]}`)},
		{"email_pre_scan", json.RawMessage(`{"kind":"email_pre_scan",
			"urgent":[],"actionable":[],"informational_count":1,"suggested_archives":[],
			"suggested_drafts":[],"needs_review":[],"scanned":1,
			"needs_you":[{"ref":1,"kind":"urgent","message_id":"m1","sender":` + q + `,"subject":` + q + `,
				"why":` + q + `,"detail":[` + q + `,` + q + `],"due_hint":` + q + `}],
			"needs_you_total":1,
			"bulk":{"count":1,"filter_tests":[` + q + `]},
			"preferences_applied":{"priority_senders":[` + q + `],"low_priority_senders":[],"category_defaults":{}},
			"mailbox_errors":[{"mailbox":` + q + `,"error":` + q + `}]}`)},
		{"unknown_card", json.RawMessage(`{"anything":` + q + `}`)},
	}
}

func TestWrapTerminatesOnAnsiStyledWords(t *testing.T) {
	// ansi.Truncate re-emits a terminating reset, so the truncated head is not
	// always a literal prefix of its input. Advancing by TrimPrefix therefore
	// made no progress and the loop spun forever.
	for name, s := range hostileStrings {
		for _, w := range []int{1, 2, 3, 5, 20, 80} {
			func() {
				done := make(chan []string, 1)
				go func() { done <- wrap(s, w) }()
				select {
				case got := <-done:
					for _, line := range got {
						if visualLen(line) > w {
							t.Errorf("wrap(%s, %d) produced a %d-wide line", name, w, visualLen(line))
						}
					}
				case <-time.After(2 * time.Second):
					t.Fatalf("wrap(%s, %d) never returned", name, w)
				}
			}()
		}
	}
}
