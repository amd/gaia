// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
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
	next, _ = m.Update(hybrid.ClosedMsg{})
	m = next.(ChatModel)
	if m.hybridPanel != nil {
		t.Fatal("still open")
	}
}

func TestModelPaletteOffersHybrid(t *testing.T) {
	found := false
	for _, c := range filterModelCommands("hyb") {
		if c.Name == "/model hybrid" && strings.Contains(c.Desc, "Hybrid (DeepSeek + Opus) (cloud)") {
			found = true
		}
	}
	if !found {
		t.Fatalf("/model hybrid missing from the model picker: %+v", filterModelCommands("hyb"))
	}
}
