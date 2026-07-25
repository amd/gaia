package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestTable(t *testing.T) {
	out := Render("table", raw(t, `{
	  "title": "Recent runs",
	  "columns": ["id", "status", "note"],
	  "rows": [
	    ["r-1", "ok", "finished in 4s"],
	    ["r-2", "failed", "timed out waiting for the model"],
	    [3, true, null]
	  ]
	}`), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Recent runs",
		"id", "status", "note",
		"r-1", "finished in 4s",
		// number / boolean / null cells all degrade to plain text.
		"3", "true",
	)
}

func TestTableRequiresColumns(t *testing.T) {
	out := Render("table", raw(t, `{"rows": [["a"]]}`), width80)
	assertContains(t, out, "Invalid table payload", "columns is required")
}

func TestTableRejectsNonScalarCell(t *testing.T) {
	// The contract's cell type is string|number|boolean|null — an object is not
	// silently stringified, it is a schema failure with the payload attached.
	out := Render("table", raw(t, `{"columns":["a"],"rows":[[{"nested":1}]]}`), width80)
	assertContains(t, out, "Invalid table payload", "raw data:")
}

// A 500-row table is spec-legal and still a scroll trap, so primitives are
// bounded like every other card. What matters is that nothing vanishes quietly:
// the truncation line must account for every entry not drawn.
func TestTableIsBoundedAndCountsEveryHiddenRow(t *testing.T) {
	for _, total := range []int{3, 25, 120, renderCap + 7} {
		rows := make([][]string, total)
		for i := range rows {
			rows[i] = []string{"row" + itoa(i)}
		}
		payload, err := json.Marshal(map[string]any{"columns": []string{"name"}, "rows": rows})
		if err != nil {
			t.Fatal(err)
		}

		out := Render("table", payload, width80)
		assertWidth(t, out, width80)
		assertBoundedAndFullyAccounted(t, out, "row", total)
	}
}

func TestListIsBoundedAndCountsEveryHiddenItem(t *testing.T) {
	for _, total := range []int{3, 25, 120, renderCap + 3} {
		items := make([]string, total)
		for i := range items {
			items[i] = "entry" + itoa(i)
		}
		payload, err := json.Marshal(map[string]any{"items": items})
		if err != nil {
			t.Fatal(err)
		}

		out := Render("list", payload, width80)
		assertWidth(t, out, width80)
		assertBoundedAndFullyAccounted(t, out, "entry", total)
	}
}

func TestKeyValueIsBoundedAndCountsEveryHiddenItem(t *testing.T) {
	for _, total := range []int{3, 25, 120, renderCap + 5} {
		kv := make([]map[string]any, total)
		for i := range kv {
			kv[i] = map[string]any{"key": "field" + itoa(i), "value": "v" + itoa(i)}
		}
		payload, err := json.Marshal(map[string]any{"items": kv})
		if err != nil {
			t.Fatal(err)
		}

		out := Render("key_value", payload, width80)
		assertWidth(t, out, width80)
		assertBoundedAndFullyAccounted(t, out, "field", total)
	}
}

// assertBoundedAndFullyAccounted checks the two things that must hold for every
// bounded card: it fits the row budget, and drawn + reported-as-hidden equals
// everything the producer sent. A card that silently drops entries reads as
// "this is all of it", which is the failure the truncation line exists to stop.
func assertBoundedAndFullyAccounted(t *testing.T, rendered, marker string, total int) {
	t.Helper()
	got := plain(rendered)

	if n := len(strings.Split(got, "\n")); n > maxCardRows+2 {
		t.Errorf("card is %d lines, want at most %d", n, maxCardRows+2)
	}

	drawn := 0
	for _, line := range strings.Split(got, "\n") {
		if strings.Contains(line, marker) {
			drawn++
		}
	}
	if drawn == 0 {
		t.Fatalf("card drew no %q rows at all:\n%s", marker, got)
	}

	hidden := 0
	for _, line := range strings.Split(got, "\n") {
		body := strings.TrimSpace(strings.Trim(line, "│"))
		if rest, ok := strings.CutPrefix(body, "+"); ok && strings.HasSuffix(rest, "more (truncated)") {
			hidden = atoi(t, strings.TrimSpace(strings.TrimSuffix(rest, "more (truncated)")))
		}
	}
	if drawn+hidden != total {
		t.Errorf("card drew %d and reported %d hidden = %d, but the producer sent %d:\n%s",
			drawn, hidden, drawn+hidden, total, got)
	}
}

func TestTableEmptyRows(t *testing.T) {
	out := Render("table", raw(t, `{"columns":["a","b"],"rows":[]}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "(no rows)")
}

func TestKeyValue(t *testing.T) {
	out := Render("key_value", raw(t, `{
	  "title": "Connection",
	  "items": [
	    {"key": "account", "value": "you@gmail.com"},
	    {"key": "messages scanned", "value": 25},
	    {"key": "autonomous", "value": false},
	    {"key": "last error", "value": null}
	  ]
	}`), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out, "Connection", "account", "you@gmail.com", "messages scanned", "25", "false")
}

func TestKeyValueEmpty(t *testing.T) {
	out := Render("key_value", raw(t, `{"items":[]}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "Details", "(no items)")
}

func TestList(t *testing.T) {
	ordered := Render("list", raw(t, `{"title":"Next steps","ordered":true,"items":["reply to Sarah","archive the digest"]}`), width80)
	t.Logf("\n%s", plain(ordered))
	assertWidth(t, ordered, width80)
	assertContains(t, ordered, "Next steps", "1. reply to Sarah", "2. archive the digest")

	unordered := Render("list", raw(t, `{"items":["alpha","beta"]}`), width80)
	assertWidth(t, unordered, width80)
	// ASCII markers only — a bullet glyph is not reliable across terminal fonts.
	assertContains(t, unordered, "- alpha", "- beta")
}

func TestListValuesAreNotInterpretedAsMarkdown(t *testing.T) {
	// §4.3: values render as plain text; markdown inside them is NOT interpreted.
	out := Render("list", raw(t, `{"items":["**bold** and <b>html</b>"]}`), width80)
	assertContains(t, out, "**bold** and <b>html</b>")
}

func TestImageDegradesToCaption(t *testing.T) {
	out := Render("image", raw(t, `{"src":"data:image/png;base64,iVBORw0KGgo=","alt":"chart","caption":"Weekly volume"}`), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	// Both caption and alt appear — the picture is never drawn, so every word
	// describing it is all the reader gets.
	assertContains(t, out, "[image: Weekly volume — chart]", "Not shown — a terminal cannot display it.")
	// The base64 payload is never dumped into the transcript.
	assertNotContains(t, out, "iVBORw0KGgo=")

	// A caption that merely repeats the alt text is not printed twice.
	same := Render("image", raw(t, `{"src":"data:image/png;base64,iVBORw0KGgo=","alt":"chart","caption":"chart"}`), width80)
	assertContains(t, same, "[image: chart]")

	// Neither present still names the card rather than blanking.
	none := Render("image", raw(t, `{"src":"data:image/gif;base64,R0lGOD"}`), width80)
	assertContains(t, none, "[image: untitled]")
}

func TestImageRejectsRemoteAndSVGSrc(t *testing.T) {
	for _, src := range []string{
		"https://example.com/chart.png",
		"data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
		"",
	} {
		out := Render("image", raw(t, `{"src":`+quote(src)+`}`), width80)
		assertContains(t, out, "Invalid image payload", "inline base64 raster")
	}
}

func TestPrimitivesDegradeAtNarrowWidth(t *testing.T) {
	payloads := map[string]string{
		"table":     `{"columns":["a","b","c"],"rows":[["one","two","three"]]}`,
		"key_value": `{"items":[{"key":"a really quite long key","value":"and a really quite long value"}]}`,
		"list":      `{"items":["a fairly long list item that will not fit"]}`,
	}
	for key, payload := range payloads {
		for _, w := range []int{18, 24, 40, 76} {
			out := Render(key, raw(t, payload), w)
			assertWidth(t, out, w)
			if strings.TrimSpace(plain(out)) == "" {
				t.Errorf("%s at width %d rendered nothing", key, w)
			}
		}
	}
}

func quote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
