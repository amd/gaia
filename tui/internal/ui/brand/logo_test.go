package brand

import (
	"strings"
	"testing"

	"github.com/charmbracelet/x/ansi"
)

// robotRows is the height every layout budgets for the mascot. Banner's
// compact rule and CompactHeightRows are both sized against it, so a redraw
// that changes the row count has to change them too.
const robotRows = 20

func TestArtIsTwentyRows(t *testing.T) {
	if len(robotArt) != robotRows {
		t.Fatalf("mascot is %d rows, want %d — CompactHeightRows is sized against it",
			len(robotArt), robotRows)
	}
	if got := strings.Count(Robot(), "\n"); got != robotRows {
		t.Errorf("Robot() rendered %d lines, want %d", got, robotRows)
	}
}

func TestEveryArtRowFitsItsDeclaredWidth(t *testing.T) {
	for i, line := range robotArt {
		if w := len([]rune(line)); w > ArtWidth {
			t.Errorf("art row %d is %d columns, past the declared ArtWidth of %d: %q",
				i, w, ArtWidth, line)
		}
	}
	for i, line := range strings.Split(strings.TrimRight(Robot(), "\n"), "\n") {
		if w := ansi.StringWidth(line); w > ArtWidth {
			t.Errorf("rendered art row %d is %d columns, past ArtWidth %d", i, w, ArtWidth)
		}
	}
}

// Robot() has no default case, so an unmapped glyph would render as bare
// uncoloured text and nothing would say so. This is what says so.
func TestEveryArtGlyphHasAStyle(t *testing.T) {
	styles := artStyles()
	for i, line := range robotArt {
		for _, ch := range line {
			if ch == ' ' {
				continue
			}
			if _, ok := styles[ch]; !ok {
				t.Errorf("art row %d uses %q, which artStyles() does not map", i, string(ch))
			}
		}
	}
}

// 80x24 is the standard minimum terminal. Twenty rows of mascot above a
// checklist leaves the checklist nowhere to go, which is the bug the old
// hub layout test was written for.
func TestBannerDropsTheMascotOnAShortTerminal(t *testing.T) {
	banner := ansi.Strip(Banner(80, 24))

	if lines := strings.Count(banner, "\n") + 1; lines > 1 {
		t.Errorf("compact banner is %d lines, want 1:\n%s", lines, banner)
	}
	if !strings.Contains(banner, "G A I A") {
		t.Errorf("compact banner dropped the wordmark entirely:\n%s", banner)
	}
	if w := ansi.StringWidth(banner); w > 80 {
		t.Errorf("compact banner is %d columns wide at width 80", w)
	}
}

// The compact form is a fallback for small terminals, not a downgrade for
// everyone.
func TestBannerKeepsTheMascotWhenThereIsRoom(t *testing.T) {
	banner := ansi.Strip(Banner(120, CompactHeightRows))

	if lines := strings.Count(banner, "\n"); lines < robotRows {
		t.Errorf("banner at %d rows is only %d lines — the mascot is missing:\n%s",
			CompactHeightRows, lines, banner)
	}
	if !strings.Contains(banner, TaglineText) {
		t.Errorf("full banner is missing the tagline %q", TaglineText)
	}
}

// A narrow terminal wraps the art into gibberish, so width compacts too.
func TestBannerCompactsOnANarrowTerminal(t *testing.T) {
	banner := ansi.Strip(Banner(ArtWidth, 60))
	if lines := strings.Count(banner, "\n") + 1; lines > 1 {
		t.Errorf("banner kept the mascot at %d columns (%d lines):\n%s", ArtWidth, lines, banner)
	}
}

// Bubble Tea renders once BEFORE the first WindowSizeMsg, against an assumed
// 80x24. A 23-row banner there overflows, scrolls the terminal, and misaligns
// the cursor-relative repaint for the whole session — the chat view then draws
// in the wrong place and looks broken. An unknown size must stay small.
func TestBannerCompactsOnAnUnknownSize(t *testing.T) {
	banner := ansi.Strip(Banner(0, 0))
	if lines := strings.Count(banner, "\n") + 1; lines > 1 {
		t.Errorf("banner rendered %d lines at an unknown size:\n%s", lines, banner)
	}
	if !strings.Contains(banner, "G A I A") {
		t.Error("the compact banner dropped the wordmark")
	}
}

// The tagline said "Local AI Agent Hub" while the hub was the product. It is
// not, and a tagline naming a screen that no longer exists is a lie the whole
// launch opens with.
func TestTaglineDoesNotAdvertiseAHub(t *testing.T) {
	if strings.Contains(strings.ToLower(TaglineText), "hub") {
		t.Errorf("tagline still names the Agent Hub: %q", TaglineText)
	}
}
