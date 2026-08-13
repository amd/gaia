package cards

import (
	"encoding/json"
	"strings"
	"testing"
)

// unifiedJSON builds a `diff` card payload from a raw unified-diff string,
// JSON-encoding it so callers can write the diff text as a plain Go string
// literal instead of hand-escaping newlines and quotes.
func unifiedJSON(t *testing.T, title, unified string) json.RawMessage {
	t.Helper()
	payload, err := json.Marshal(map[string]any{"title": title, "unified": unified})
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func TestDiffBasicAdditionAndDeletion(t *testing.T) {
	unified := "--- a/greet.py\n" +
		"+++ b/greet.py\n" +
		"@@ -1,3 +1,3 @@\n" +
		" def greet():\n" +
		"-    print('hi')\n" +
		"+    print('hello')\n" +
		"     return None\n"
	out := Render("diff", unifiedJSON(t, "greet.py", unified), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	assertContains(t, out,
		"┌─ greet.py ",
		"@@ -1,3 +1,3 @@",
		"print('hi')",
		"print('hello')",
		"return None",
	)
	// The file-header lines are redundant with the box title and are dropped.
	assertNotContains(t, out, "--- a/greet.py", "+++ b/greet.py")

	got := plain(out)
	addLine := lineContaining(got, "print('hello')")
	delLine := lineContaining(got, "print('hi')")
	if !strings.Contains(addLine, "+") {
		t.Errorf("addition line missing '+' marker: %q", addLine)
	}
	if !strings.Contains(delLine, "-") {
		t.Errorf("deletion line missing '-' marker: %q", delLine)
	}
}

func TestDiffCarriesLineNumbers(t *testing.T) {
	unified := "--- a/f.txt\n+++ b/f.txt\n@@ -10,3 +10,3 @@\n line10\n-line11\n+line11-changed\n line12\n"
	out := Render("diff", unifiedJSON(t, "f.txt", unified), width80)
	got := plain(out)
	t.Logf("\n%s", got)

	// Old line 11 (removed) and new line 11 (added) both appear as gutter
	// numbers -- context lines 10/12 use their (unchanged) position.
	assertContains(t, got, "10", "11", "12")
}

func TestDiffNewFileIsPureAddition(t *testing.T) {
	unified := "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+line one\n+line two\n"
	out := Render("diff", unifiedJSON(t, "new.txt", unified), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "+ line one", "+ line two")
	assertNotContains(t, out, "/dev/null")
}

func TestDiffFullDeletionIsPureRemoval(t *testing.T) {
	unified := "--- a/gone.txt\n+++ b/gone.txt\n@@ -1,2 +0,0 @@\n-line one\n-line two\n"
	out := Render("diff", unifiedJSON(t, "gone.txt", unified), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "- line one", "- line two")
}

func TestDiffNoChangesShowsHonestEmptyState(t *testing.T) {
	out := Render("diff", unifiedJSON(t, "same.txt", ""), width80)
	assertWidth(t, out, width80)
	assertContains(t, out, "(no changes)")
}

func TestDiffMissingUnifiedIsInvalid(t *testing.T) {
	out := Render("diff", raw(t, `{"title":"f.txt"}`), width80)
	assertContains(t, out, "Invalid diff payload", "unified is required", "raw data:")
}

func TestDiffMalformedJSONIsInvalid(t *testing.T) {
	// Deliberately truncated/invalid JSON -- raw() would refuse to build this
	// fixture (it asserts validity), so the RawMessage is constructed directly.
	out := Render("diff", json.RawMessage(`{"title":"f.txt","unified":`), width80)
	assertContains(t, out, "Invalid diff payload")
}

func TestDiffDefaultsTitleWhenAbsent(t *testing.T) {
	out := Render("diff", raw(t, `{"unified":"@@ -1 +1 @@\n-a\n+b\n"}`), width80)
	assertContains(t, out, "Diff")
}

// A diff long enough to exceed maxDiffCardRows must fold into a truncation
// footer that accounts for every hidden line, never silently drop them.
func TestDiffLargeDiffIsTruncatedWithFooter(t *testing.T) {
	var sb strings.Builder
	sb.WriteString("--- a/big.txt\n+++ b/big.txt\n")
	total := maxDiffCardRows + 75
	sb.WriteString("@@ -1," + itoa(total) + " +1," + itoa(total) + " @@\n")
	for i := 0; i < total; i++ {
		sb.WriteString("-old" + itoa(i) + "\n")
	}
	out := Render("diff", unifiedJSON(t, "big.txt", sb.String()), width80)
	t.Logf("\n%s", plain(out))

	assertWidth(t, out, width80)
	got := plain(out)
	if n := len(strings.Split(got, "\n")); n > maxDiffCardRows+3 {
		t.Errorf("diff card is %d lines, want at most %d (bounded)", n, maxDiffCardRows+3)
	}
	if !strings.Contains(got, "more line") || !strings.Contains(got, "truncated") {
		t.Errorf("large diff missing a truncation footer:\n%s", got)
	}
	// Content is still legible -- the first lines are shown, not a blank card.
	assertContains(t, out, "old0")
}

func TestDiffContentIsNotInterpretedAsMarkdownOrEscapes(t *testing.T) {
	unified := "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-**bold**\n+<script>evil()</script>\n"
	out := Render("diff", unifiedJSON(t, "f.txt", unified), width80)
	assertContains(t, out, "**bold**", "<script>evil()</script>")
}

func TestDiffDegradesAtNarrowWidth(t *testing.T) {
	unified := "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n-a line that is fairly long and will not fit narrow\n+a replacement line that is also fairly long indeed\n"
	for _, w := range []int{18, 24, 40, 76} {
		out := Render("diff", unifiedJSON(t, "f.txt", unified), w)
		assertWidth(t, out, w)
		if strings.TrimSpace(plain(out)) == "" {
			t.Errorf("diff at width %d rendered nothing", w)
		}
	}
}

// A diff line embedding a raw ESC byte (a file that happens to contain one,
// or a hostile producer) must not be able to inject its own styling — clean()
// strips it before the card's own coloring is applied.
func TestDiffLineControlCharsAreStripped(t *testing.T) {
	unified := "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-old\n+new\x1b[31minjected\x1b[0m\n"
	out := Render("diff", unifiedJSON(t, "f.txt", unified), width80)
	// The raw escape byte must not survive into the rendered output as a
	// literal control character outside of this package's own styling.
	if strings.ContainsRune(out, 0x1b) && !strings.Contains(out, "injected") {
		t.Errorf("expected injected text to survive stripped of its own escapes: %q", out)
	}
	assertContains(t, out, "injected")
}

func lineContaining(text, substr string) string {
	for _, line := range strings.Split(text, "\n") {
		if strings.Contains(line, substr) {
			return line
		}
	}
	return ""
}
