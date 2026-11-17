// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/hybridapi"
	"github.com/amd/gaia/tui/internal/hybridapi/hybridapitest"
	"github.com/amd/gaia/tui/internal/ui/hybrid"
)

func hybridFinal(answer, route string) event.CanonicalFinalEvent {
	return event.CanonicalFinalEvent{Type: "final", Answer: answer, Route: []byte(route)}
}

func lastAnswer(t *testing.T, m ChatModel) Message {
	t.Helper()
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleAssistant {
			return m.messages[i]
		}
	}
	t.Fatal("no answer")
	return Message{}
}

func TestHybridTurnNamesItsRouteUnderTheAnswer(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-3 * time.Second)
	m = feed(t, m, hybridFinal("Done.",
		`{"model":"amd.DeepSeek-V4.1-Flash","kind":"write_prose","difficulty":1,"stakes":"low","classified":true}`))
	msg := lastAnswer(t, m)
	if msg.Route != "→ DeepSeek (writing, d1)" {
		t.Fatalf("route %q", msg.Route)
	}
	rendered := ansi.Strip(m.renderMessage(&msg, nil))
	if !strings.Contains(rendered, "→ DeepSeek (writing, d1)") {
		t.Fatalf("not shown without --dev:\n%s", rendered)
	}
}

func TestOrdinaryTurnHasNoRoute(t *testing.T) {
	m, _ := newTestModel(t)
	m.streaming = true
	m.queryStart = time.Now().Add(-time.Second)
	m = feed(t, m, event.CanonicalFinalEvent{Type: "final", Answer: "hi"},
		hybridFinal("x", `{"kind":"other"}`), hybridFinal("y", `not json`))
	for _, msg := range m.messages {
		if msg.Route != "" {
			t.Fatalf("a route with no model must not be shown: %+v", msg)
		}
	}
}

func TestDegradedClassifierNoticeIsSaidOnce(t *testing.T) {
	m, _ := newTestModel(t)
	degraded := `{"model":"amd.Claude-Opus-5-5","classified":false,"degraded":true,"notice":"Hybrid is running everything on the strong model: the classifier keeps failing."}`
	for i := 0; i < 3; i++ {
		m.streaming = true
		m = feed(t, m, hybridFinal("ok", degraded))
	}
	count := 0
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "classifier keeps failing") {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("notice shown %d times", count)
	}
	// Recovery, then a fresh failure, says it again.
	m.streaming = true
	m = feed(t, m, hybridFinal("ok", `{"model":"amd.DeepSeek-V4.1-Flash","kind":"other","difficulty":1,"stakes":"low","classified":true}`))
	m.streaming = true
	m = feed(t, m, hybridFinal("ok", degraded))
	count = 0
	for _, msg := range m.messages {
		if msg.Role == RoleStatus && strings.Contains(msg.Content, "classifier keeps failing") {
			count++
		}
	}
	if count != 2 {
		t.Fatalf("notice after recovery shown %d times in all", count)
	}
}

func TestHybridCommandOpensAndClosesTheSettings(t *testing.T) {
	m, _ := newTestModel(t)
	next, cmd := m.submit("/hybrid")
	m = next.(ChatModel)
	if m.hybridPanel == nil || cmd == nil {
		t.Fatal("/hybrid did not open the settings")
	}
	if m.ControlSnapshot().Overlay != "hybrid" {
		t.Fatalf("overlay %q", m.ControlSnapshot().Overlay)
	}
	if !strings.Contains(ansi.Strip(m.View()), "Hybrid routing") {
		t.Fatalf("view:\n%s", ansi.Strip(m.View()))
	}
	next, cmd = m.Update(hybrid.ClosedMsg{})
	m = next.(ChatModel)
	if m.hybridPanel != nil {
		t.Fatal("still open")
	}
	// Closing re-reads hybrid's entry, so the palette names the new pair.
	if cmd == nil {
		t.Fatal("closing /hybrid did not refresh hybrid's palette entry")
	}
	if _, ok := cmd().(hybridEntryMsg); !ok {
		t.Fatal("closing /hybrid sent something other than the entry refresh")
	}
}

// countingAPI counts entry reads, to prove the palette doesn't read per key.
type countingAPI struct {
	*hybridapitest.Fake
	reads *int
}

func (c countingAPI) Entry(ctx context.Context) (hybridapi.Entry, error) {
	*c.reads++
	return c.Fake.Entry(ctx)
}

func TestModelPaletteNamesHybridFromTheEntryReadOnce(t *testing.T) {
	reads := 0
	remote := true
	orig := newHybridEntryAPI
	t.Cleanup(func() { newHybridEntryAPI = orig })
	newHybridEntryAPI = func() hybridapi.API {
		return countingAPI{Fake: &hybridapitest.Fake{EntryAnswer: hybridapi.Entry{
			ID: "hybrid", DisplayName: "Hybrid (DeepSeek + Opus)", Remote: &remote, Available: true, Shown: true}}, reads: &reads}
	}
	m := gaiaTestModel(t)
	next, _ := m.Update(fetchHybridEntryCmd()())
	m = next.(ChatModel)
	for _, r := range "/model hyb" {
		m = typeInto(t, m, string(r))
	}
	found := false
	for _, c := range m.paletteFiltered() {
		if c.Name == "/model hybrid" && strings.Contains(c.Desc, "Hybrid (DeepSeek + Opus) (cloud)") {
			found = true
		}
	}
	if !found {
		t.Fatalf("/model hybrid missing or unnamed: %+v", m.paletteFiltered())
	}
	if reads != 1 {
		t.Fatalf("the entry was read %d times; typing must not read it", reads)
	}
}

func TestModelPaletteSaysWhenHybridCantBeNamed(t *testing.T) {
	if got := hybridPaletteDesc(nil, errors.New("down")); !strings.Contains(got, "/hybrid says why") {
		t.Fatal(got)
	}
	reason := "Local only is on"
	e := hybridapi.Entry{DisplayName: "Hybrid (Gemma + Opus)", Reason: &reason, Shown: true}
	if got := hybridPaletteDesc(&e, nil); !strings.Contains(got, "can't use yet") {
		t.Fatal(got)
	}
}

func TestTheModelPaletteRereadsTheEntryOncePerOpening(t *testing.T) {
	m := gaiaTestModel(t)
	m = typeInto(t, m, "/model h")
	if !m.palette.open || !m.hybridEntryAsked {
		t.Fatal("opening the /model palette did not re-read hybrid's entry")
	}
	if m.refreshHybridEntry() != nil {
		t.Fatal("typing in an open /model palette read the entry again")
	}
	m.input.SetValue("")
	m.syncPalette()
	if m.refreshHybridEntry() != nil || m.hybridEntryAsked {
		t.Fatal("a closed palette read the entry, or kept its mark")
	}
	m = typeInto(t, m, "/model ")
	if !m.hybridEntryAsked {
		t.Fatal("reopening the /model palette did not re-read hybrid's entry")
	}
}

func TestHybridIsLeftOutOfThePaletteWhenNothingCanRun(t *testing.T) {
	e := hybridapi.Entry{DisplayName: "Hybrid (A + B)", Shown: false}
	if got := hybridPaletteDesc(&e, nil); got != "" {
		t.Fatalf("desc %q", got)
	}
	for _, c := range filterModelCommands("hyb", "") {
		if c.Name == "/model hybrid" {
			t.Fatal("hybrid offered though the backend says nothing can run")
		}
	}
}
