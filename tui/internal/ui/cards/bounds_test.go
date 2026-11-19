package cards

import (
	"strings"
	"testing"
)

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

// Wide runes must not shear a border: a CJK cell is two columns per glyph.
func TestWideRunesDoNotShearTheFrame(t *testing.T) {
	data := raw(t, `{"title":"四半期レビュー","columns":["差出人","件名"],`+
		`"rows":[["田中太郎 <tanaka@example.jp>","四半期レビューの締め切りが近づいています"]]}`)

	for _, w := range []int{24, 40, 76} {
		out := Render("table", data, w)
		t.Logf("width %d:\n%s", w, plain(out))
		assertWidth(t, out, w)
	}
}
