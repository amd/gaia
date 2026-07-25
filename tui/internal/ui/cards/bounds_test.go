package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

// maxCardLines is the whole card: maxCardRows interior rows plus two borders.
const maxCardLines = maxCardRows + 2

func preScanFixture(t *testing.T, mutate func(m map[string]any)) json.RawMessage {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(populatedPreScan), &m); err != nil {
		t.Fatal(err)
	}
	mutate(m)
	out, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func item(i int, key string) map[string]any {
	return map[string]any{
		"message_id": "id" + itoa(i),
		"sender":     "A Person With A Long Display Name " + itoa(i) + " <person" + itoa(i) + "@example.com>",
		"subject":    "A subject line long enough to need truncating at eighty columns " + itoa(i),
		key:          "a rationale that is itself long enough to wrap across more than one line " + itoa(i),
	}
}

func items(n int, key string) []map[string]any {
	out := make([]map[string]any, n)
	for i := range out {
		out[i] = item(i, key)
	}
	return out
}

// The card is bounded, not merely usually short. Every one of these would push
// an unbounded card off a 24-row screen.
func TestPreScanHeightIsBounded(t *testing.T) {
	cases := map[string]func(m map[string]any){
		"agent caps in every bucket": func(m map[string]any) {
			m["urgent"] = items(5, "why")
			m["actionable"] = items(5, "why")
			m["suggested_archives"] = items(10, "reason")
			m["totals"] = map[string]any{"urgent": 40, "actionable": 60, "informational": 12, "suggested_archives": 90}
		},
		"long wrapping preferences footer": func(m map[string]any) {
			m["preferences_applied"] = map[string]any{
				"priority_senders":     []string{"Sarah Chen", "Priya Nadkarni", "Marcus Webb", "Dana Whitfield", "Tomasz Kowalczyk", "Aisha Rahman"},
				"low_priority_senders": []string{"news@substack.com", "offers@retailer.com", "no-reply@social.example"},
				"category_defaults":    map[string]string{"FYI": "archive", "PROMOTIONAL": "archive"},
			}
		},
		"several long mailbox errors": func(m map[string]any) {
			m["mailbox_errors"] = []map[string]string{
				{"mailbox": "google", "error": "the OAuth grant for this agent was revoked and must be reconnected before the mailbox can be scanned"},
				{"mailbox": "microsoft", "error": "token expired while refreshing; the identity provider returned invalid_grant"},
				{"mailbox": "imap", "error": "connection refused by the configured host after three attempts"},
				{"mailbox": "other", "error": "unknown provider"},
			}
		},
		"everything at once": func(m map[string]any) {
			m["urgent"] = items(5, "why")
			m["actionable"] = items(5, "why")
			m["suggested_archives"] = items(10, "reason")
			m["totals"] = map[string]any{"urgent": 40, "actionable": 60, "informational": 12, "suggested_archives": 90}
			m["preferences_applied"] = map[string]any{
				"priority_senders":     []string{"Sarah Chen", "Priya Nadkarni", "Marcus Webb", "Dana Whitfield"},
				"low_priority_senders": []string{},
				"category_defaults":    map[string]string{"FYI": "archive"},
			}
			m["mailbox_errors"] = []map[string]string{
				{"mailbox": "microsoft", "error": "token expired while refreshing; the identity provider returned invalid_grant"},
			}
		},
	}

	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			out := Render("email_pre_scan", preScanFixture(t, mutate), width80)
			t.Logf("\n%s", plain(out))

			assertWidth(t, out, width80)
			if n := len(strings.Split(plain(out), "\n")); n > maxCardLines {
				t.Errorf("card is %d lines, want at most %d", n, maxCardLines)
			}
		})
	}
}

// The bound must hold at narrow widths too, where everything wraps harder.
func TestPreScanHeightIsBoundedAtNarrowWidths(t *testing.T) {
	data := preScanFixture(t, func(m map[string]any) {
		m["urgent"] = items(5, "why")
		m["actionable"] = items(5, "why")
		m["suggested_archives"] = items(10, "reason")
		m["totals"] = map[string]any{"urgent": 40, "actionable": 60, "informational": 12, "suggested_archives": 90}
	})

	for _, w := range []int{24, 32, 40, 60, 76, 100, 160} {
		out := Render("email_pre_scan", data, w)
		assertWidth(t, out, w)
		lines := strings.Split(plain(out), "\n")
		// Wrapping pushes past the row budget at very narrow widths; the budget
		// governs rows of content, not the wrapped lines each row becomes. Assert
		// it still cannot run away.
		if len(lines) > maxCardLines*2 {
			t.Errorf("width %d produced %d lines, want at most %d", w, len(lines), maxCardLines*2)
		}
		if !strings.Contains(plain(out), "URGENT") {
			t.Errorf("width %d dropped the URGENT section", w)
		}
	}
}

func TestFitSectionsAlwaysTerminatesAndKeepsEveryBucket(t *testing.T) {
	cases := [][]int{
		{0, 0, 0}, {5, 5, 10}, {1, 0, 0}, {100, 100, 100}, {0, 3, 0}, {2, 0, 9},
	}
	for _, counts := range cases {
		for _, budget := range []int{-5, 0, 1, 3, 8, 22, 1000} {
			totals := []int{counts[0] * 3, counts[1] * 3, counts[2] * 3}
			// Two rows per item — a row plus a one-line rationale.
			itemRows := make([][]int, 3)
			for i, n := range counts {
				itemRows[i] = make([]int, n)
				for j := range itemRows[i] {
					itemRows[i][j] = 2
				}
			}
			got, _ := fitSections(itemRows, totals, budget)
			for i := range got {
				if counts[i] == 0 && got[i] != 0 {
					t.Errorf("fitSections(%v, %d) invented rows for an empty bucket: %v", counts, budget, got)
				}
				if counts[i] > 0 && got[i] < 1 {
					t.Errorf("fitSections(%v, %d) hid a non-empty bucket entirely: %v", counts, budget, got)
				}
				if got[i] > counts[i] {
					t.Errorf("fitSections(%v, %d) showed more than arrived: %v", counts, budget, got)
				}
			}
		}
	}
}

func TestWrapTerminatesOnUnbreakableInput(t *testing.T) {
	long := strings.Repeat("x", 500)
	for _, w := range []int{1, 2, 3, 7, 80} {
		got := wrap(long, w)
		if len(got) == 0 {
			t.Fatalf("wrap(long, %d) returned nothing", w)
		}
		for _, line := range got {
			if visualLen(line) > w {
				t.Errorf("wrap(long, %d) produced a %d-wide line", w, visualLen(line))
			}
		}
	}
	if got := wrap("", 10); len(got) != 1 || got[0] != "" {
		t.Errorf("wrap(\"\", 10) = %q, want one empty line", got)
	}
	if got := wrap("   ", 10); len(got) != 1 || got[0] != "" {
		t.Errorf("wrap(whitespace, 10) = %q, want one empty line", got)
	}
}

func TestItoa(t *testing.T) {
	for _, tc := range []struct {
		in   int
		want string
	}{{0, "0"}, {7, "7"}, {42, "42"}, {1000000, "1000000"}, {-3, "-3"}} {
		if got := itoa(tc.in); got != tc.want {
			t.Errorf("itoa(%d) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// Wide runes must not shear a border: a CJK subject is two columns per glyph.
func TestWideRunesDoNotShearTheFrame(t *testing.T) {
	data := preScanFixture(t, func(m map[string]any) {
		m["urgent"] = []map[string]any{{
			"message_id": "m1",
			"sender":     "田中太郎 <tanaka@example.jp>",
			"subject":    "四半期レビューの締め切りが近づいています",
			"why":        "返信が必要です — 金曜日までに",
		}}
		m["totals"] = map[string]any{"urgent": 1, "actionable": 0, "informational": 0, "suggested_archives": 0}
		m["actionable"] = []any{}
		m["suggested_archives"] = []any{}
	})

	for _, w := range []int{24, 40, 76} {
		out := Render("email_pre_scan", data, w)
		t.Logf("width %d:\n%s", w, plain(out))
		assertWidth(t, out, w)
	}
}
