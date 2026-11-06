// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package phonepanel

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/phone"
	"github.com/amd/gaia/tui/internal/phone/phonetest"
)

// drive runs cmd and feeds the panel's own results back, as Bubble Tea would.
// Cursor blinks and other foreign messages are dropped.
func drive(t *testing.T, m Model, cmd tea.Cmd) Model {
	t.Helper()
	queue := []tea.Cmd{cmd}
	for len(queue) > 0 {
		c := queue[0]
		queue = queue[1:]
		if c == nil {
			continue
		}
		msg := c()
		if batch, ok := msg.(tea.BatchMsg); ok {
			queue = append(queue, batch...)
			continue
		}
		if _, ok := msg.(Msg); !ok {
			continue
		}
		next, more := m.Update(msg)
		m = next.(Model)
		queue = append(queue, more)
	}
	return m
}

func press(t *testing.T, m Model, keys ...string) Model {
	t.Helper()
	for _, k := range keys {
		var msg tea.KeyMsg
		switch k {
		case "enter":
			msg = tea.KeyMsg{Type: tea.KeyEnter}
		case "esc":
			msg = tea.KeyMsg{Type: tea.KeyEsc}
		case "tab":
			msg = tea.KeyMsg{Type: tea.KeyTab}
		case "down":
			msg = tea.KeyMsg{Type: tea.KeyDown}
		case "up":
			msg = tea.KeyMsg{Type: tea.KeyUp}
		default:
			msg = tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
		}
		next, cmd := m.Update(msg)
		m = drive(t, next.(Model), cmd)
	}
	return m
}

// typeText types into the focused field.
func typeText(t *testing.T, m Model, s string) Model {
	t.Helper()
	next, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)})
	return next.(Model)
}

func open(t *testing.T, f *phonetest.Fake) Model {
	t.Helper()
	m := New(f.Client(), 100, 32)
	return drive(t, m, m.Init())
}

func screen(m Model) string { return ansi.Strip(m.View()) }

func selectItem(t *testing.T, m Model, id string) Model {
	t.Helper()
	for i, it := range m.items() {
		if it.id == id {
			m.cursor = i
			return m
		}
	}
	t.Fatalf("no row %q in %v", id, m.items())
	return m
}

func body(t *testing.T, r phonetest.Request) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal([]byte(r.Body), &out); err != nil {
		t.Fatalf("%s %s body %q: %v", r.Method, r.Path, r.Body, err)
	}
	return out
}

func TestSetupFlowSimulatedNumberCodeOnAndTest(t *testing.T) {
	f := phonetest.New(t)
	m := open(t, f)
	if !strings.Contains(screen(m), "○ Off") || !strings.Contains(screen(m), "Simulated phone") {
		t.Fatalf("header:\n%s", screen(m))
	}

	m = press(t, selectItem(t, m, "simulated"), "enter")
	if !strings.Contains(screen(m), "from +15550100000") {
		t.Fatalf("simulated provider not shown:\n%s", screen(m))
	}

	// Phone book: add a number, then the code.
	m = press(t, m, "tab", "a")
	m = typeText(t, m, "+1 555 123 4567")
	m = press(t, m, "enter")
	m = typeText(t, m, "Mobile")
	m = press(t, m, "enter", "enter")
	if m.form == nil || m.form.kind != "code_add" {
		t.Fatalf("no code step after adding; err=%q\n%s", m.errMsg, screen(m))
	}
	if got := body(t, f.Last("POST", "/contacts")); got["via"] != "call" || got["label"] != "Mobile" {
		t.Fatalf("add sent %v", got)
	}
	m = typeText(t, m, "000000")
	m = press(t, m, "enter")
	if m.form == nil || !strings.Contains(m.errMsg, "tries left") {
		t.Fatalf("a wrong code should keep the form and say tries left; err=%q", m.errMsg)
	}
	m.form.fields[0].SetValue(phonetest.Code)
	m = press(t, m, "enter")
	if m.form != nil || !strings.Contains(screen(m), "4567") || !strings.Contains(screen(m), "first to ring") {
		t.Fatalf("number not in the book; err=%q\n%s", m.errMsg, screen(m))
	}

	// Turn on, then test.
	m = press(t, m, "1")
	m = press(t, selectItem(t, m, "power"), "enter")
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, map[string]any{"enabled": true}) {
		t.Fatalf("turning on sent %v", got)
	}
	m = press(t, selectItem(t, m, "test"), "enter")
	if !strings.Contains(screen(m), "● On") || !strings.Contains(screen(m), "Alerts (1)") {
		t.Fatalf("after the test:\n%s", screen(m))
	}
	if !strings.Contains(screen(m), "call to •••4567") {
		t.Fatalf("the simulated log should show the call:\n%s", screen(m))
	}
}

func TestASecondNumberAsksForTheFirstNumbersCode(t *testing.T) {
	f := phonetest.New(t)
	f.Contacts = []phone.Contact{{ID: 9, Number: "•••1111", Primary: true}}
	f.Settings["from_number"] = "+15550100000"
	m := press(t, open(t, f), "2", "a")
	m = typeText(t, m, "+15552222222")
	m = press(t, m, "enter", "enter", "enter")
	if m.form == nil || len(m.form.fields) != 2 || !strings.Contains(screen(m), "•••1111") {
		t.Fatalf("no second code field naming the first number:\n%s", screen(m))
	}
	m.form.fields[0].SetValue(phonetest.Code)
	m.form.fields[1].SetValue(phonetest.Code)
	m = press(t, m, "down", "enter")
	if got := body(t, f.Last("POST", "/contacts/confirm")); got["confirm_code"] != phonetest.Code {
		t.Fatalf("confirm sent %v", got)
	}
}

func TestTwilioKeysErrorShowsAndTheTokenIsNeverOnScreen(t *testing.T) {
	f := phonetest.New(t)
	f.KeysError = &phone.Error{Code: "bad_credentials", Message: "Twilio rejected the Account SID or Auth Token."}
	m := press(t, selectItem(t, open(t, f), "twilio"), "enter")
	m = typeText(t, m, "AC123")
	m = press(t, m, "enter")
	m = typeText(t, m, "s3cret-token")
	if strings.Contains(screen(m), "s3cret") {
		t.Fatal("the auth token is readable on screen")
	}
	m = press(t, m, "enter")
	m = typeText(t, m, "+15550109999")
	m = press(t, m, "enter")
	if got := body(t, f.Last("POST", "/keys")); got["auth_token"] != "s3cret-token" {
		t.Fatalf("keys sent %v", got)
	}
	view := screen(m)
	if !strings.Contains(view, "Twilio rejected the Account SID or Auth Token.") || m.form == nil {
		t.Fatalf("the provider's refusal isn't shown, or the form closed:\n%s", view)
	}
	if strings.Contains(view, "s3cret") || m.form.fields[1].Value() != "" {
		t.Fatal("the token survived the attempt")
	}
}

func TestRemoveANumberNeedsItsCode(t *testing.T) {
	f := phonetest.New(t)
	f.Contacts = []phone.Contact{{ID: 3, Number: "•••4567", Primary: true}, {ID: 4, Number: "•••8888"}}
	m := press(t, open(t, f), "2", "down", "x")
	if m.form == nil || m.form.kind != "code_remove" || !strings.Contains(screen(m), "Remove •••8888") {
		t.Fatalf("no remove code step:\n%s", screen(m))
	}
	m.form.fields[0].SetValue(phonetest.Code)
	m = press(t, m, "enter")
	if strings.Contains(screen(m), "8888") || len(f.Contacts) != 1 {
		t.Fatalf("number not removed:\n%s", screen(m))
	}
}

func TestRuleEditsSendOnlyTheChangedKeys(t *testing.T) {
	f := phonetest.New(t)
	m := press(t, open(t, f), "3")
	m = press(t, selectItem(t, m, "rule:schedule_failed"), "s")
	want := map[string]any{"rules": map[string]any{"schedule_failed": map[string]any{"steps": "notify"}}}
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, want) {
		t.Fatalf("steps sent %v", got)
	}
	m = press(t, selectItem(t, m, "rule:agent"), "enter")
	want = map[string]any{"rules": map[string]any{"agent": map[string]any{"on": true}}}
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, want) {
		t.Fatalf("on sent %v", got)
	}
	m = press(t, selectItem(t, m, "rule:approval_waiting"), "+")
	want = map[string]any{"rules": map[string]any{"approval_waiting": map[string]any{"after_minutes": float64(15)}}}
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, want) {
		t.Fatalf("after_minutes sent %v", got)
	}
	// Limits: only the one that changed.
	m = press(t, selectItem(t, m, "limits"), "enter")
	m.form.fields[0].SetValue("8")
	m = press(t, m, "enter", "enter", "enter")
	want = map[string]any{"limits": map[string]any{"calls_per_day": float64(8)}}
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, want) {
		t.Fatalf("limits sent %v", got)
	}
	// Quiet hours, then off.
	m = press(t, selectItem(t, m, "quiet"), "enter")
	m = typeText(t, m, "22:00")
	m = press(t, m, "enter")
	m = typeText(t, m, "07:00")
	m = press(t, m, "enter")
	want = map[string]any{"quiet_hours": map[string]any{"start": "22:00", "end": "07:00"}}
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, want) {
		t.Fatalf("quiet hours sent %v", got)
	}
	if !strings.Contains(screen(m), "22:00–07:00") {
		t.Fatalf("quiet hours not shown:\n%s", screen(m))
	}
}

func TestTurningOnWithoutANumberSaysWhy(t *testing.T) {
	f := phonetest.New(t)
	m := press(t, selectItem(t, open(t, f), "power"), "enter")
	if !strings.Contains(screen(m), "Add your number to the phone book") {
		t.Fatalf("refusal not shown:\n%s", screen(m))
	}
}

func TestAlertsListAndMarkHandled(t *testing.T) {
	f := phonetest.New(t)
	f.Alerts = []phone.Alert{{ID: 7, Title: "Scheduled job failed", Count: 3, State: "open", Held: "quiet_hours",
		Deliveries: []phone.Delivery{{Channel: "text", Number: "•••4567", State: "done", Outcome: "delivered"}}}}
	m := press(t, open(t, f), "4")
	view := screen(m)
	for _, want := range []string{"Scheduled job failed", "3 times", "Held for quiet hours", "text to •••4567: done (delivered)"} {
		if !strings.Contains(view, want) {
			t.Fatalf("alerts view lacks %q:\n%s", want, view)
		}
	}
	m = press(t, m, "enter")
	if f.Alerts[0].State != "handled" || !strings.Contains(screen(m), "Marked handled") {
		t.Fatalf("not handled:\n%s", screen(m))
	}
}

func TestADaemonWithoutPhoneSaysSo(t *testing.T) {
	f := phonetest.New(t)
	f.Missing = true
	m := open(t, f)
	if !strings.Contains(screen(m), "arrive when GAIA updates") {
		t.Fatalf("404 not explained:\n%s", screen(m))
	}
	next, cmd := m.Update(tea.KeyMsg{Type: tea.KeyEsc})
	if _, ok := cmd().(CloseMsg); !ok || next == nil {
		t.Fatal("Esc does not close the panel")
	}
}

func TestViewFitsTheWindow(t *testing.T) {
	f := phonetest.New(t)
	f.Contacts = []phone.Contact{{ID: 3, Number: "•••4567", Label: "Mobile", Primary: true}}
	m := open(t, f)
	for _, size := range [][2]int{{100, 32}, {50, 14}, {30, 8}} {
		next, _ := m.Update(tea.WindowSizeMsg{Width: size[0], Height: size[1]})
		mm := next.(Model)
		for s := 0; s < 4; s++ {
			mm = mm.goSection(s)
			lines := strings.Split(mm.View(), "\n")
			if len(lines) != size[1] {
				t.Errorf("%dx%d section %d: %d rows", size[0], size[1], s, len(lines))
			}
			for i, l := range lines {
				if w := ansi.StringWidth(l); w > size[0] {
					t.Errorf("%dx%d section %d row %d: %d wide", size[0], size[1], s, i, w)
				}
			}
		}
	}
}

func TestClickingATabAndARow(t *testing.T) {
	f := phonetest.New(t)
	m := open(t, f)
	_, hits := m.render()
	for _, h := range hits {
		if h.tab && h.index == int(secRules) {
			next, _ := m.Update(tea.MouseMsg{X: h.x0, Y: h.line, Button: tea.MouseButtonLeft, Action: tea.MouseActionPress})
			m = next.(Model)
		}
	}
	if m.sec != secRules {
		t.Fatalf("click on the Rules tab left section %d", m.sec)
	}
}
