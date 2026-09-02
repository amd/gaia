package test

import (
	"fmt"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/ui/brand"
	"github.com/amd/gaia/tui/internal/ui/chat"
)

// maxPumpSteps bounds the command loop so a bug that keeps re-issuing a command
// fails the test instead of hanging it.
const maxPumpSteps = 200

// isCursorBlink matches bubbles/cursor's blink messages. One of the two types
// is unexported, so this matches on the type name rather than skipping the
// whole cursor package.
func isCursorBlink(msg tea.Msg) bool {
	name := fmt.Sprintf("%T", msg)
	return strings.HasPrefix(name, "cursor.") && strings.Contains(name, "BlinkMsg")
}

// windowSize is the resize message every model test starts with.
func windowSize(w, h int) tea.WindowSizeMsg {
	return tea.WindowSizeMsg{Width: w, Height: h}
}

func key(s string) tea.KeyMsg {
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func keyEnter() tea.KeyMsg { return tea.KeyMsg{Type: tea.KeyEnter} }
func keyEsc() tea.KeyMsg   { return tea.KeyMsg{Type: tea.KeyEscape} }

// Frame zero is what a user sees before anything has been probed. It has to
// name the product — a launch that opens on a blank terminal while a Python
// interpreter starts reads as a hang.
func TestTheFirstFrameIsTheSplash(t *testing.T) {
	isolateGaiaHome(t)
	d := newLocalDriver(t, "gaia-agent-not-here", 120, 40)

	if got := d.view(); got != "splash" {
		t.Fatalf("first view = %q, want splash", got)
	}
	frame := d.screen()
	if !strings.Contains(frame, "G A I A") {
		t.Errorf("the first frame does not carry the wordmark:\n%s", frame)
	}
	// One signature row of the mascot, so a banner that silently stopped
	// rendering the art fails here rather than passing on the wordmark alone.
	if !strings.Contains(frame, "+#############*=") {
		t.Errorf("the first frame does not carry the mascot:\n%s", frame)
	}
	if !strings.Contains(frame, brand.TaglineText) {
		t.Errorf("the first frame does not carry the tagline:\n%s", frame)
	}
}

// The splash is a frame, not a destination: the launch walks off it on its own.
func TestTheLaunchWalksSplashToPreflight(t *testing.T) {
	isolateGaiaHome(t)
	d := newLocalDriver(t, "gaia-agent-not-here", 120, 40)

	d.launch()

	if got := d.view(); got != "preflight" {
		t.Fatalf("view after launch = %q, want preflight", got)
	}
}

// Nothing about the hub may survive on screen. A partial deletion that left a
// tab bar or an install hint would still compile.
func TestNoScreenStillTalksAboutAHub(t *testing.T) {
	isolateGaiaHome(t)
	d := newLocalDriver(t, "gaia-agent-not-here", 120, 40)
	splash := d.screen()
	d.launch()

	for _, frame := range []string{splash, d.screen()} {
		for _, gone := range []string{
			"Installed (", "Available (", "Coming Soon", "i install", "Agent Hub",
		} {
			if strings.Contains(frame, gone) {
				t.Errorf("a screen still says %q:\n%s", gone, frame)
			}
		}
	}
}

func TestChatModelWelcome(t *testing.T) {
	m := chat.NewChatModel(nil, "test-agent", "", false)

	// View before window size — should show welcome
	if view := m.View(); !strings.Contains(view, "Welcome to GAIA") {
		t.Error("chat view missing welcome message before window size")
	}

	updated, _ := m.Update(windowSize(120, 40))
	view := updated.(chat.ChatModel).View()

	if !strings.Contains(view, "Welcome to GAIA") {
		t.Error("chat view missing welcome message after window size")
	}
	if !strings.Contains(view, "test-agent") {
		t.Error("chat view missing agent name")
	}
	// The way out has to be on screen somewhere; it no longer has to be in the
	// composer placeholder. That line now teaches Alt+Enter — the affordance
	// nobody guesses — while the status bar carries quit on every frame.
	if !strings.Contains(view, "Ctrl+C") {
		t.Error("chat view missing quit hint")
	}
}

// Esc used to dispatch a "back to the hub" message. There is no hub, and a
// message nothing consumes would leave the user in an alt screen with no way
// out — so Esc must neither quit nor dispatch anything.
func TestEscOnAnIdleChatIsSafe(t *testing.T) {
	m := chat.NewChatModelForFlagship(nil, "gaia", "GAIA", false, true)

	updated, _ := m.Update(windowSize(120, 40))
	m = updated.(chat.ChatModel)

	if _, cmd := m.Update(keyEsc()); cmd != nil {
		if _, quit := cmd().(tea.QuitMsg); quit {
			t.Fatal("esc destroyed the session; Ctrl+C is the way out")
		}
	}

	_, cmd := m.Update(tea.KeyMsg{Type: tea.KeyCtrlC})
	if cmd == nil {
		t.Fatal("Ctrl+C produced no command")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatalf("Ctrl+C produced %T, want tea.QuitMsg", cmd())
	}
}

func TestBinaryDiscovery(t *testing.T) {
	cat := catalog.NewCatalog()
	cat.DiscoverBinaries()

	gaia := cat.Get(catalog.FlagshipID)
	if gaia == nil {
		t.Fatal("the flagship agent is not in the catalog")
	}
	if gaia.BinaryPath == "" {
		t.Fatal("discovery cleared the flagship's binary name")
	}
	t.Logf("flagship binary resolved to: %s", gaia.BinaryPath)
}

// stripAnsi removes ANSI escape sequences from a string.
func stripAnsi(s string) string {
	var result []byte
	i := 0
	for i < len(s) {
		if s[i] == '\x1b' && i+1 < len(s) && s[i+1] == '[' {
			j := i + 2
			for j < len(s) && !((s[j] >= 'A' && s[j] <= 'Z') || (s[j] >= 'a' && s[j] <= 'z') || s[j] == '~') {
				j++
			}
			if j < len(s) {
				j++ // skip the terminating character
			}
			i = j
		} else {
			result = append(result, s[i])
			i++
		}
	}
	return string(result)
}
