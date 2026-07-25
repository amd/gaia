package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

// Contract §7: an unrecognised `render` degrades to a visible generic card with
// the raw payload attached. Never blank, never dropped — a silently swallowed
// card is how a turn appears to do nothing.
func TestUnknownRenderFallsBackToGenericCard(t *testing.T) {
	out := Render("some_future_card", raw(t, `{"title":"Q3","widget":{"kind":"sparkline","points":[1,2,3]}}`), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"Unsupported card",
		`Unsupported card type: "some_future_card"`,
		"raw data:",
		// The payload is reachable so the producer stays debuggable.
		"sparkline",
	)
}

// `diff` is in the contract's primitive list but has no producer today, so it is
// deliberately not built. It must ride the unsupported fallback, not blank.
func TestDiffRidesTheUnsupportedFallback(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"config","unified":"@@ -1 +1 @@\n-a\n+b\n"}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, `Unsupported card type: "diff"`, "raw data:")
}

func TestEmptyRenderKeyStillDrawsSomething(t *testing.T) {
	out := Render("", raw(t, `{"a":1}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "Unsupported card type", "raw data:")
}

func TestFallbackWithEmptyData(t *testing.T) {
	out := Render("mystery", nil, width80)
	assertWidth(t, out, width80)
	assertContains(t, out, `Unsupported card type: "mystery"`, "raw data: (empty)")
}

func TestFallbackWithNonJSONData(t *testing.T) {
	// A producer that sent bytes rather than JSON is still shown verbatim.
	out := Render("mystery", json.RawMessage("not json at all"), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "not json at all")
}

func TestRawDumpIsBounded(t *testing.T) {
	items := make([]string, 200)
	for i := range items {
		items[i] = "entry" + itoa(i)
	}
	payload, err := json.Marshal(map[string]any{"items": items})
	if err != nil {
		t.Fatal(err)
	}

	out := Render("mystery", payload, width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "more lines (truncated)")
	if n := len(strings.Split(plain(out), "\n")); n > rawDumpLines+10 {
		t.Errorf("fallback card is %d lines; the raw dump must stay bounded", n)
	}
}

func TestRenderNeverReturnsEmpty(t *testing.T) {
	// The one invariant that matters everywhere: whatever comes off the wire,
	// something visible comes back.
	keys := []string{"email_pre_scan", "table", "key_value", "list", "image", "diff", "", "🙂", "unknown"}
	payloads := []json.RawMessage{
		nil,
		json.RawMessage(`null`),
		json.RawMessage(`{}`),
		json.RawMessage(`[]`),
		json.RawMessage(`"a string"`),
		json.RawMessage(`{"unexpected": {"deeply": {"nested": true}}}`),
		json.RawMessage(`garbage`),
	}
	for _, key := range keys {
		for _, payload := range payloads {
			for _, w := range []int{10, 24, 76, 200} {
				out := Render(key, payload, w)
				if strings.TrimSpace(plain(out)) == "" {
					t.Fatalf("Render(%q, %s, %d) returned nothing", key, payload, w)
				}
				assertWidth(t, out, w)
			}
		}
	}
}
