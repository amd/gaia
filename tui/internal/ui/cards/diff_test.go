package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

// An approved edit_file sends its change as a `diff` card (#3710); the terminal
// must draw the hunk, not the unsupported-card fallback.
func TestDiffDrawsTheUnifiedText(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"config.py","unified":"--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n-debug = False\n+debug = True\n"}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "config.py", "@@ -1 +1 @@", "-debug = False", "+debug = True")
	assertNotContains(t, out, "Unsupported card", "raw data:")
}

func TestDiffShowsBadgeAndNote(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"a.txt","badge":"Edited","note":"Showing the first 400 of 900 diff lines.","unified":"+x"}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "Edited · a.txt", "Showing the first 400 of 900 diff lines.", "+x")
}

func TestDiffWithoutTitleStillHasOne(t *testing.T) {
	out := Render("diff", raw(t, `{"unified":"+x"}`), width80)
	assertContains(t, out, "Diff", "+x")
}

func TestDiffEmptyUnifiedSaysSo(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"a.txt","unified":""}`), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "(no changes)")
}

func TestDiffMissingUnifiedIsInvalid(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"a.txt"}`), width80)
	assertContains(t, out, "Invalid diff payload: unified is required", "raw data:")
}

func TestDiffWrongTypeIsInvalid(t *testing.T) {
	out := Render("diff", raw(t, `{"unified":42}`), width80)
	assertContains(t, out, "Invalid diff payload")
}

func TestDiffCRLFAndEscapesCannotShearTheCard(t *testing.T) {
	payload, err := json.Marshal(map[string]string{
		"title":   "win.txt",
		"unified": "-old\r\n+new \x1b[31mred\x1b[0m\r\n+" + strings.Repeat("w", 300),
	})
	if err != nil {
		t.Fatal(err)
	}
	out := Render("diff", payload, width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "-old", "+new red")
	if strings.ContainsRune(out, '\r') || strings.ContainsRune(out, 0x1b) {
		t.Errorf("diff card leaked a control character:\n%q", out)
	}
}

func TestDiffLongBodyIsBoundedAndCounted(t *testing.T) {
	lines := make([]string, 300)
	for i := range lines {
		lines[i] = "+line" + itoa(i)
	}
	payload, err := json.Marshal(map[string]string{"unified": strings.Join(lines, "\n")})
	if err != nil {
		t.Fatal(err)
	}
	out := Render("diff", payload, width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "+line0", "more lines (truncated)")
	if n := len(strings.Split(plain(out), "\n")); n > maxCardRows+3 {
		t.Errorf("diff card is %d lines; it must stay bounded", n)
	}
	shown := maxCardRows - 1
	assertContains(t, out, "… "+itoa(300-shown)+" more lines (truncated)")
}

func TestDiffNarrowTerminalStillRenders(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"a.txt","unified":"+x"}`), 12)
	assertWidth(t, out, 12)
	assertContains(t, out, "+x")
}
