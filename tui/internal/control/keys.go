package control

import (
	"fmt"
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// namedKeys maps a key name to the tea.KeyType that produces it.
//
// The names are taken from bubbletea itself (KeyType.String()), so
// KeyMsgFor(name).String() round-trips back to name for every entry. That is
// the property the TUI's own handlers switch on — HubModel.handleKey compares
// msg.String() against "tab", "shift+tab", "enter" and friends — so a name that
// does not round-trip would silently do the wrong thing. The round-trip is
// asserted in TestKeyNamesRoundTrip.
var namedKeys = map[string]tea.KeyType{}

// keyAliases maps caller-friendly spellings to a canonical name in namedKeys.
var keyAliases = map[string]string{
	"escape":    "esc",
	"return":    "enter",
	"pageup":    "pgup",
	"page_up":   "pgup",
	"pgdn":      "pgdown",
	"pagedown":  "pgdown",
	"page_down": "pgdown",
	"del":       "delete",
	"space":     " ",
	"spacebar":  " ",
	"ins":       "insert",
	"bs":        "backspace",
}

func init() {
	keyTypes := []tea.KeyType{
		tea.KeyEnter, tea.KeyEsc, tea.KeyTab, tea.KeyShiftTab, tea.KeyBackspace,
		tea.KeyUp, tea.KeyDown, tea.KeyLeft, tea.KeyRight,
		tea.KeyPgUp, tea.KeyPgDown, tea.KeyHome, tea.KeyEnd,
		tea.KeyDelete, tea.KeyInsert, tea.KeySpace,
		tea.KeyCtrlA, tea.KeyCtrlB, tea.KeyCtrlC, tea.KeyCtrlD, tea.KeyCtrlE,
		tea.KeyCtrlF, tea.KeyCtrlG, tea.KeyCtrlJ, tea.KeyCtrlK, tea.KeyCtrlL,
		tea.KeyCtrlN, tea.KeyCtrlO, tea.KeyCtrlP, tea.KeyCtrlQ, tea.KeyCtrlR,
		tea.KeyCtrlS, tea.KeyCtrlT, tea.KeyCtrlU, tea.KeyCtrlV, tea.KeyCtrlW,
		tea.KeyCtrlX, tea.KeyCtrlY, tea.KeyCtrlZ,
		tea.KeyCtrlUp, tea.KeyCtrlDown, tea.KeyCtrlLeft, tea.KeyCtrlRight,
		tea.KeyCtrlHome, tea.KeyCtrlEnd, tea.KeyCtrlPgUp, tea.KeyCtrlPgDown,
		tea.KeyShiftUp, tea.KeyShiftDown, tea.KeyShiftLeft, tea.KeyShiftRight,
		tea.KeyShiftHome, tea.KeyShiftEnd,
		tea.KeyF1, tea.KeyF2, tea.KeyF3, tea.KeyF4, tea.KeyF5, tea.KeyF6,
		tea.KeyF7, tea.KeyF8, tea.KeyF9, tea.KeyF10, tea.KeyF11, tea.KeyF12,
	}
	for _, kt := range keyTypes {
		name := kt.String()
		if name == "" {
			// A bubbletea upgrade dropped a name we depend on. Fail at startup
			// rather than silently accepting a key that maps to nothing.
			panic(fmt.Sprintf("control: bubbletea KeyType %d has no name", int(kt)))
		}
		namedKeys[name] = kt
	}
}

// KeyMsgFor converts a key name into the tea.KeyMsg the TUI's handlers expect.
//
// Accepted forms, in precedence order:
//
//	named key   "enter", "esc", "tab", "shift+tab", "up", "pgdown", "ctrl+c", "f5"
//	alias       "escape", "return", "pgdn", "pageup", "space", "del"
//	alt+<rune>  "alt+x"  (a single rune with the Alt modifier)
//	single rune "?", "/", "q", "Y"  (case is preserved)
//
// A three-letter name like "tab" is the Tab key, never the runes t-a-b. Sending
// runes is what POST /control/v1/text is for.
func KeyMsgFor(name string) (tea.KeyMsg, error) {
	if name == "" {
		return tea.KeyMsg{}, fmt.Errorf("empty key name")
	}

	// Checked before trimming: a lone " " is bubbletea's own name for the space
	// key, and trimming would turn it into an empty, unmatchable name.
	if name == " " {
		return tea.KeyMsg{Type: tea.KeySpace, Runes: []rune{' '}}, nil
	}

	lookup := strings.ToLower(strings.TrimSpace(name))
	if canonical, ok := keyAliases[lookup]; ok {
		lookup = canonical
	}
	if kt, ok := namedKeys[lookup]; ok {
		if kt == tea.KeySpace {
			// bubbles' text inputs insert from Runes, not from the type, so a
			// bare KeySpace types nothing at all.
			return tea.KeyMsg{Type: tea.KeySpace, Runes: []rune{' '}}, nil
		}
		return tea.KeyMsg{Type: kt}, nil
	}

	if strings.HasPrefix(lookup, "alt+") {
		runes := []rune(strings.TrimPrefix(strings.TrimSpace(name), "alt+"))
		if len(runes) != 1 {
			return tea.KeyMsg{}, fmt.Errorf("%q is not a supported key: alt+ accepts exactly one character (e.g. \"alt+x\")", name)
		}
		return tea.KeyMsg{Type: tea.KeyRunes, Runes: runes, Alt: true}, nil
	}

	// Case is preserved for literal characters: "Y" and "y" are different keys
	// to a confirmation dialog.
	if runes := []rune(strings.TrimSpace(name)); len(runes) == 1 {
		return tea.KeyMsg{Type: tea.KeyRunes, Runes: runes}, nil
	}

	return tea.KeyMsg{}, fmt.Errorf("%q is not a supported key name; pass a single character to type it, or one of: %s",
		name, strings.Join(SupportedKeys(), ", "))
}

// SupportedKeys lists every named key, sorted, for error messages and docs.
func SupportedKeys() []string {
	names := make([]string, 0, len(namedKeys)+len(keyAliases))
	for name := range namedKeys {
		if name == " " {
			name = "space"
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// TextKeyMsgs converts a string into one KeyMsg per rune, which is how a human
// typing at the keyboard reaches the model.
func TextKeyMsgs(text string) []tea.KeyMsg {
	runes := []rune(text)
	msgs := make([]tea.KeyMsg, 0, len(runes))
	for _, r := range runes {
		if r == ' ' {
			msgs = append(msgs, tea.KeyMsg{Type: tea.KeySpace, Runes: []rune{' '}})
			continue
		}
		msgs = append(msgs, tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{r}})
	}
	return msgs
}
