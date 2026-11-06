// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package phonepanel

import (
	"reflect"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/phone"
	"github.com/amd/gaia/tui/internal/phone/phonetest"
	"github.com/amd/gaia/tui/internal/speech"
)

func TestTalkPinSetAndClearNeverShowsThePin(t *testing.T) {
	f := phonetest.New(t)
	m := press(t, open(t, f), "5")
	if !strings.Contains(screen(m), "Set a PIN") {
		t.Fatalf("status should ask for a PIN:\n%s", screen(m))
	}
	m = press(t, selectItem(t, m, "pin"), "enter")
	m = typeText(t, m, "12ab")
	if m.form.fields[0].Value() != "12" {
		t.Fatalf("the PIN field took letters: %q", m.form.fields[0].Value())
	}
	m = typeText(t, m, "3490")
	if strings.Contains(screen(m), "123490") {
		t.Fatal("the PIN is readable on screen")
	}
	m = press(t, m, "enter")
	if f.Pin != "123490" || m.form != nil || strings.Contains(screen(m), "123490") {
		t.Fatalf("PIN not set (fake has %q), or still on screen:\n%s", f.Pin, screen(m))
	}
	if !strings.Contains(screen(m), "Change the PIN") || !strings.Contains(screen(m), "Turn on mobile access") {
		t.Fatalf("after setting the PIN:\n%s", screen(m))
	}
	m = press(t, selectItem(t, m, "pin_clear"), "enter")
	if f.Pin != "" || !strings.Contains(screen(m), "Set a PIN") {
		t.Fatalf("PIN not cleared:\n%s", screen(m))
	}
}

func TestATooShortPinIsRefusedAndCleared(t *testing.T) {
	f := phonetest.New(t)
	m := press(t, selectItem(t, press(t, open(t, f), "5"), "pin"), "enter")
	m = typeText(t, m, "12")
	m = press(t, m, "enter")
	if f.Last("POST", "/pin").Method != "" || !strings.Contains(m.errMsg, "4 to 8 digits") || m.form.fields[0].Value() != "" {
		t.Fatalf("short PIN: sent=%v err=%q", f.Last("POST", "/pin"), m.errMsg)
	}
}

func TestTalkToggleAndVoiceSendOnlyThatKey(t *testing.T) {
	f := phonetest.New(t)
	m := press(t, selectItem(t, press(t, open(t, f), "5"), "talk"), "enter")
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, map[string]any{"talk_on_calls": false}) {
		t.Fatalf("toggle sent %v", got)
	}
	if !strings.Contains(screen(m), "Talking on calls is off") {
		t.Fatalf("status after turning off:\n%s", screen(m))
	}
	m = press(t, selectItem(t, m, "voice"), "enter")
	if !m.picking || !strings.Contains(screen(m), "Nicole") {
		t.Fatalf("voice list:\n%s", screen(m))
	}
	m = press(t, m, "down", "enter")
	want := speech.Voices()[m.pickIndexOf("af_nicole")+1].ID
	if got := body(t, f.Last("PUT", "/settings")); !reflect.DeepEqual(got, map[string]any{"voice": want}) {
		t.Fatalf("voice sent %v, want %s", got, want)
	}
}

func TestTalkStatusStates(t *testing.T) {
	base := phone.Settings{TalkOnCalls: true, PinSet: true, TalkAvailable: true}
	cases := []struct {
		edit  func(*phone.Settings)
		ready bool
		want  string
	}{
		{func(*phone.Settings) {}, true, "Ready: calls can talk"},
		{func(s *phone.Settings) { s.PinSet = false }, false, "Set a PIN"},
		{func(s *phone.Settings) { s.TalkAvailable = false }, false, "Turn on mobile access so calls can reach this PC"},
		{func(s *phone.Settings) { s.TalkOnCalls = false }, false, "Talking on calls is off"},
	}
	for _, c := range cases {
		s := base
		c.edit(&s)
		ready, text := phone.TalkStatus(s)
		if ready != c.ready || text != c.want {
			t.Errorf("%+v: got (%t, %q)", s, ready, text)
		}
	}
	f := phonetest.New(t)
	f.Settings["pin_set"], f.Settings["talk_available"] = true, true
	if m := press(t, open(t, f), "5"); !strings.Contains(screen(m), "Ready: calls can talk") {
		t.Fatalf("ready state:\n%s", screen(m))
	}
}

func (m Model) pickIndexOf(id string) int {
	for i, v := range speech.Voices() {
		if v.ID == id {
			return i
		}
	}
	return -1
}
