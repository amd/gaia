package control

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// TestKeyNamesRoundTrip is the contract test: the TUI's own handlers switch on
// tea.KeyMsg.String(), so every name we accept must produce a message whose
// String() is that same name. A name that does not round-trip silently does
// something else.
func TestKeyNamesRoundTrip(t *testing.T) {
	for _, name := range SupportedKeys() {
		msg, err := KeyMsgFor(name)
		if err != nil {
			t.Fatalf("KeyMsgFor(%q) returned an error: %v", name, err)
		}
		want := name
		if name == "space" {
			want = " " // bubbletea's own name for KeySpace
		}
		if got := msg.String(); got != want {
			t.Errorf("KeyMsgFor(%q).String() = %q, want %q", name, got, want)
		}
	}
}

// TestNamedKeysAreNotRunes pins the bug the existing smoke tests contain:
// tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("tab")} is the three runes
// t-a-b, not the Tab key, so HubModel.handleKey never sees "tab".
func TestNamedKeysAreNotRunes(t *testing.T) {
	for _, name := range []string{"tab", "enter", "esc", "up", "down", "pgup", "delete"} {
		msg, err := KeyMsgFor(name)
		if err != nil {
			t.Fatalf("KeyMsgFor(%q): %v", name, err)
		}
		if msg.Type == tea.KeyRunes {
			t.Errorf("KeyMsgFor(%q) produced KeyRunes %q — that is the literal characters, not the key", name, string(msg.Runes))
		}
		if len(msg.Runes) != 0 {
			t.Errorf("KeyMsgFor(%q) carried runes %q; a named key carries none", name, string(msg.Runes))
		}
	}
}

// TestSpaceKeyCarriesItsRune pins a bug that answers 200 while typing nothing:
// bubbles' textinput/textarea insert from msg.Runes, so a KeySpace with no
// runes is silently dropped and the caller sees a success response.
func TestSpaceKeyCarriesItsRune(t *testing.T) {
	for _, name := range []string{"space", "spacebar", " "} {
		msg, err := KeyMsgFor(name)
		if err != nil {
			t.Fatalf("KeyMsgFor(%q): %v", name, err)
		}
		if msg.Type != tea.KeySpace {
			t.Errorf("KeyMsgFor(%q).Type = %d, want KeySpace", name, msg.Type)
		}
		if len(msg.Runes) != 1 || msg.Runes[0] != ' ' {
			t.Errorf("KeyMsgFor(%q).Runes = %q, want a single space — text inputs insert from Runes", name, string(msg.Runes))
		}
	}
}

func TestKeyMsgForSupportedNames(t *testing.T) {
	cases := []struct {
		name     string
		wantType tea.KeyType
		wantStr  string
	}{
		{"enter", tea.KeyEnter, "enter"},
		{"esc", tea.KeyEsc, "esc"},
		{"escape", tea.KeyEsc, "esc"},
		{"return", tea.KeyEnter, "enter"},
		{"tab", tea.KeyTab, "tab"},
		{"shift+tab", tea.KeyShiftTab, "shift+tab"},
		{"up", tea.KeyUp, "up"},
		{"down", tea.KeyDown, "down"},
		{"left", tea.KeyLeft, "left"},
		{"right", tea.KeyRight, "right"},
		{"pgup", tea.KeyPgUp, "pgup"},
		{"pgdn", tea.KeyPgDown, "pgdown"},
		{"pgdown", tea.KeyPgDown, "pgdown"},
		{"pageup", tea.KeyPgUp, "pgup"},
		{"pagedown", tea.KeyPgDown, "pgdown"},
		{"backspace", tea.KeyBackspace, "backspace"},
		{"delete", tea.KeyDelete, "delete"},
		{"del", tea.KeyDelete, "delete"},
		{"home", tea.KeyHome, "home"},
		{"end", tea.KeyEnd, "end"},
		{"ctrl+c", tea.KeyCtrlC, "ctrl+c"},
		{"ctrl+d", tea.KeyCtrlD, "ctrl+d"},
		{"CTRL+C", tea.KeyCtrlC, "ctrl+c"},
		{"space", tea.KeySpace, " "},
		{"f5", tea.KeyF5, "f5"},
		{"?", tea.KeyRunes, "?"},
		{"/", tea.KeyRunes, "/"},
		{"q", tea.KeyRunes, "q"},
		{"v", tea.KeyRunes, "v"},
	}
	for _, tc := range cases {
		msg, err := KeyMsgFor(tc.name)
		if err != nil {
			t.Errorf("KeyMsgFor(%q): %v", tc.name, err)
			continue
		}
		if msg.Type != tc.wantType {
			t.Errorf("KeyMsgFor(%q).Type = %d, want %d", tc.name, msg.Type, tc.wantType)
		}
		if got := msg.String(); got != tc.wantStr {
			t.Errorf("KeyMsgFor(%q).String() = %q, want %q", tc.name, got, tc.wantStr)
		}
	}
}

func TestKeyMsgForPreservesCase(t *testing.T) {
	upper, err := KeyMsgFor("Y")
	if err != nil {
		t.Fatalf("KeyMsgFor(\"Y\"): %v", err)
	}
	if upper.String() != "Y" {
		t.Errorf("KeyMsgFor(\"Y\").String() = %q, want %q — a confirm dialog treats Y and y differently", upper.String(), "Y")
	}
}

func TestKeyMsgForAlt(t *testing.T) {
	msg, err := KeyMsgFor("alt+x")
	if err != nil {
		t.Fatalf("KeyMsgFor(\"alt+x\"): %v", err)
	}
	if !msg.Alt || msg.String() != "alt+x" {
		t.Errorf("KeyMsgFor(\"alt+x\") = %+v (String %q), want the Alt modifier", msg, msg.String())
	}
	if _, err := KeyMsgFor("alt+abc"); err == nil {
		t.Error("KeyMsgFor(\"alt+abc\") should be rejected — alt+ takes exactly one character")
	}
}

func TestKeyMsgForRejectsUnknown(t *testing.T) {
	for _, name := range []string{"", "supertab", "ctrl+shift+meta+q", "hyper"} {
		_, err := KeyMsgFor(name)
		if err == nil {
			t.Errorf("KeyMsgFor(%q) should have failed", name)
			continue
		}
		if name != "" && !strings.Contains(err.Error(), name) {
			t.Errorf("KeyMsgFor(%q) error %q should quote the offending name", name, err)
		}
	}
}

func TestTextKeyMsgs(t *testing.T) {
	msgs := TextKeyMsgs("hi there")
	if len(msgs) != len("hi there") {
		t.Fatalf("TextKeyMsgs produced %d messages, want %d", len(msgs), len("hi there"))
	}
	var got strings.Builder
	for _, m := range msgs {
		got.WriteString(m.String())
	}
	if got.String() != "hi there" {
		t.Errorf("TextKeyMsgs round-trip = %q, want %q", got.String(), "hi there")
	}
	// The space must arrive as KeySpace with its rune set — that is what
	// bubbles/textarea inserts from.
	space := msgs[2]
	if space.Type != tea.KeySpace || len(space.Runes) != 1 || space.Runes[0] != ' ' {
		t.Errorf("space key = %+v, want KeySpace carrying ' '", space)
	}
}

func TestSupportedKeysIncludesTheDocumentedSet(t *testing.T) {
	supported := map[string]bool{}
	for _, name := range SupportedKeys() {
		supported[name] = true
	}
	for _, required := range []string{
		"enter", "esc", "tab", "shift+tab", "up", "down", "left", "right",
		"pgup", "pgdown", "backspace", "ctrl+c", "ctrl+d", "space",
	} {
		if !supported[required] {
			t.Errorf("SupportedKeys() is missing %q", required)
		}
	}
}
