package components

import (
	"regexp"
	"strings"
	"testing"
)

// A grey block on screen is a space painted with a background. Find any run of
// spaces at the end of a line that the terminal would paint.
func paintedTrailingSpaces(line string) bool {
	bg := false
	trailingPainted := false
	for i := 0; i < len(line); {
		if params, next, ok := sgrAt(line, i); ok {
			bg = applySGR(bg, params)
			i = next
			continue
		}
		if line[i] == ' ' || line[i] == '\t' {
			if bg {
				trailingPainted = true
			}
		} else {
			trailingPainted = false
		}
		i++
	}
	return trailingPainted
}

// The reported artifact: "a minor grey area near the bottom", one stray block
// hanging off the end of a sentence wherever an inline code span wrapped.
const wrappedSpanCase = "I'm not able to create that PDF right now. The Python script hits a real bug — `height - 100` fails with `unsupported operand type(s) for +: 'float' and 'str'`, meaning `letter` isn't unpacking as expected in this execution environment."

func TestWrappedInlineCodeLeavesNoPaintedPadding(t *testing.T) {
	for w := 40; w <= 120; w++ {
		SetWordWrap(w)
		for i, line := range strings.Split(RenderMarkdown(wrappedSpanCase), "\n") {
			if paintedTrailingSpaces(line) {
				t.Errorf("width=%d line=%d ends in painted spaces: %q", w, i, line)
			}
		}
	}
}

func TestTheArtifactIsRealWithoutTheFix(t *testing.T) {
	SetWordWrap(60)
	raw, err := renderRaw(wrappedSpanCase)
	if err != nil {
		t.Skipf("renderer unavailable: %v", err)
	}
	found := false
	for _, line := range strings.Split(raw, "\n") {
		if paintedTrailingSpaces(line) {
			found = true
		}
	}
	if !found {
		t.Fatal("glamour no longer leaves the span open at a wrap — sealLineEnds " +
			"may now be guarding nothing, check before deleting it")
	}
}

// renderRaw renders without sealing, so a test can show what is being fixed.
func renderRaw(content string) (string, error) {
	mu.Lock()
	defer mu.Unlock()
	if err := buildLocked(); err != nil {
		return "", err
	}
	return renderer.Render(content)
}

var visible = regexp.MustCompile(`\x1b\[[0-9;]*m`)

func TestSealingChangesNothingVisible(t *testing.T) {
	SetWordWrap(60)
	raw, err := renderRaw(wrappedSpanCase)
	if err != nil {
		t.Skipf("renderer unavailable: %v", err)
	}
	if got, want := visible.ReplaceAllString(sealLineEnds(raw), ""),
		visible.ReplaceAllString(raw, ""); got != want {
		t.Errorf("sealing altered the text itself\n got: %q\nwant: %q", got, want)
	}
}

func TestFencedBlocksAreUntouched(t *testing.T) {
	SetWordWrap(50)
	md := "before\n\n```python\nx = 1\nprint('hello')\n```\n\nafter"
	raw, err := renderRaw(md)
	if err != nil {
		t.Skipf("renderer unavailable: %v", err)
	}
	if sealed := sealLineEnds(raw); sealed != raw {
		t.Error("a fenced block was modified; it already closes every run and " +
			"must render byte-for-byte as before")
	}
}

func TestSealLine(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{
			name: "background left open before padding gets a reset",
			in:   "\x1b[48;5;235mcode\x1b[38;5;254m   ",
			want: "\x1b[48;5;235mcode\x1b[38;5;254m\x1b[0m   ",
		},
		{
			name: "already closed is left alone",
			in:   "\x1b[48;5;235mcode\x1b[0m   ",
			want: "\x1b[48;5;235mcode\x1b[0m   ",
		},
		{
			name: "no trailing space, nothing to protect",
			in:   "\x1b[48;5;235mcode",
			want: "\x1b[48;5;235mcode",
		},
		{
			name: "foreground only is not a background",
			in:   "\x1b[38;5;254mtext   ",
			want: "\x1b[38;5;254mtext   ",
		},
		{
			name: "truecolor background",
			in:   "\x1b[48;2;30;30;30mcode   ",
			want: "\x1b[48;2;30;30;30mcode\x1b[0m   ",
		},
		{
			name: "a 0 inside a truecolor operand is not a reset",
			in:   "\x1b[48;2;0;49;0mcode   ",
			want: "\x1b[48;2;0;49;0mcode\x1b[0m   ",
		},
		{
			name: "direct background code",
			in:   "\x1b[41mcode   ",
			want: "\x1b[41mcode\x1b[0m   ",
		},
		{
			name: "49 cancels the background",
			in:   "\x1b[41mcode\x1b[49m   ",
			want: "\x1b[41mcode\x1b[49m   ",
		},
		{
			name: "bare ESC[m resets",
			in:   "\x1b[41mcode\x1b[m   ",
			want: "\x1b[41mcode\x1b[m   ",
		},
		{
			name: "empty line",
			in:   "",
			want: "",
		},
		{
			name: "spaces only",
			in:   "\x1b[41m   ",
			want: "\x1b[41m   ",
		},
		{
			name: "multibyte text before the padding",
			in:   "\x1b[48;5;235menvironment —\x1b[38;5;254m  ",
			want: "\x1b[48;5;235menvironment —\x1b[38;5;254m\x1b[0m  ",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := sealLine(tc.in); got != tc.want {
				t.Errorf("sealLine(%q)\n got %q\nwant %q", tc.in, got, tc.want)
			}
		})
	}
}
