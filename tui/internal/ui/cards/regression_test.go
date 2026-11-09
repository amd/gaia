package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

// A number's literal token is the text §4.3 says to render. Decoding through
// float64 silently rewrote the value the producer sent.
func TestNumbersKeepTheirLiteralToken(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"1234567890123456789", "1234567890123456789"},
		{"-9223372036854775808", "-9223372036854775808"},
		{"0.0000001", "0.0000001"},
		{"1e-7", "1e-7"},
		{"3.14159265358979", "3.14159265358979"},
		{"0", "0"},
		{"-0.5", "-0.5"},
	} {
		out := Render("table", json.RawMessage(`{"columns":["n"],"rows":[[`+tc.in+`]]}`), width80)
		if !strings.Contains(plain(out), tc.want) {
			t.Errorf("number %s rendered without %q:\n%s", tc.in, tc.want, plain(out))
		}
	}
}

// §4.3 marks only `title` (and `ordered`) optional. An absent `items` is a
// schema failure, not an empty card that reads as "the agent found nothing".
func TestAbsentItemsIsInvalidButEmptyItemsIsNot(t *testing.T) {
	for _, key := range []string{"key_value", "list"} {
		missing := Render(key, json.RawMessage(`{"title":"X"}`), width80)
		if !strings.Contains(plain(missing), "Invalid "+key+" payload") {
			t.Errorf("%s with no items must be invalid:\n%s", key, plain(missing))
		}

		empty := Render(key, json.RawMessage(`{"title":"X","items":[]}`), width80)
		if strings.Contains(plain(empty), "Invalid") {
			t.Errorf("%s with an explicit empty items is legitimate:\n%s", key, plain(empty))
		}
		assertContains(t, empty, "(no items)")
	}
}

const narrowTable = `{"title":"Inbox","columns":["from","subject"],"rows":[["Sarah Chen","Q3 review"]]}`

// Below the frame's minimum the card drops the border rather than clamping its
// width up — a 24-column frame inside a 12-column viewport is the same shear by
// another route.
func TestNarrowCardsDropTheFrameRatherThanOverflow(t *testing.T) {
	for _, w := range []int{4, 8, 12, 20, minCardWidth - 1} {
		out := Render("table", raw(t, narrowTable), w)
		assertWidth(t, out, w)
		if strings.Contains(plain(out), "┌") || strings.Contains(plain(out), "│") {
			t.Errorf("width %d still drew a frame it cannot fit:\n%s", w, plain(out))
		}
		if strings.TrimSpace(plain(out)) == "" {
			t.Errorf("width %d rendered nothing", w)
		}
	}

	// At the threshold and above, the frame comes back.
	out := Render("table", raw(t, narrowTable), minCardWidth)
	assertWidth(t, out, minCardWidth)
	assertContains(t, out, "┌")
}

// A table with more columns than the terminal can carry used to render every
// cell as a bare "…": no data, and no sign that anything was lost.
func TestWideTableReportsColumnsItCannotShow(t *testing.T) {
	cols := make([]string, 40)
	row := make([]string, 40)
	for i := range cols {
		cols[i] = "c" + itoa(i)
		row[i] = "v" + itoa(i)
	}
	payload, err := json.Marshal(map[string]any{"columns": cols, "rows": [][]string{row}})
	if err != nil {
		t.Fatal(err)
	}

	out := Render("table", payload, width80)
	t.Logf("\n%s", plain(out))
	assertWidth(t, out, width80)
	assertContains(t, out, "column(s) too narrow to show")

	// The columns that ARE shown must carry real content, not just ellipses.
	if !strings.Contains(plain(out), "v0") {
		t.Errorf("no column rendered any data:\n%s", plain(out))
	}
}

// The card is memoized per width; a resize must not serve the old layout.
func TestRawDumpAndTruncationSurviveHostileWidths(t *testing.T) {
	for _, w := range []int{6, 24, 80} {
		out := Render("mystery", json.RawMessage(`{"a":"`+strings.Repeat("x", 300)+`"}`), w)
		assertWidth(t, out, w)
		if strings.TrimSpace(plain(out)) == "" {
			t.Errorf("width %d rendered nothing", w)
		}
	}
}
