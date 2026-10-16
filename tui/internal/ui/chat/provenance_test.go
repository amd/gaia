// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

// researchTurn is a web-research answer: one search, two pages from one site,
// one from another, and a document search.
func researchTurn(t *testing.T, m *ChatModel, now *time.Time) {
	startTurn(m, *now, "What does it cost to camp in Yosemite this summer?")
	at(t, m, now, time.Second, call("search_web", `{"query":"Yosemite camping fees 2026"}`))
	at(t, m, now, 2*time.Second, result("search_web", `{"success":true}`))
	for _, u := range []string{
		"https://www.nps.gov/yose/planyourvisit/fees.htm",
		"https://www.nps.gov/yose/planyourvisit/camping.htm",
		"https://www.recreation.gov/camping/campgrounds/232447",
	} {
		at(t, m, now, time.Second, call("fetch_page", `{"url":`+jsonString(u)+`}`))
		at(t, m, now, 2*time.Second, result("fetch_page", `{"success":true}`))
	}
	at(t, m, now, time.Second, call("query_documents", `{"query":"campground reservation rules"}`))
	at(t, m, now, time.Second, result("query_documents", `{"success":true,"source_files":["C:/Users/me/Documents/park_guide.pdf"]}`))
	at(t, m, now, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "Campsites are $36 a night; reserve on recreation.gov."})
}

func lastAnswer(m ChatModel) Message {
	for i := len(m.messages) - 1; i >= 0; i-- {
		if m.messages[i].Role == RoleAssistant {
			return m.messages[i]
		}
	}
	return Message{}
}

func TestWebResearchListsThePagesItUsed(t *testing.T) {
	m, now := frameChat(t, 100, 40)
	researchTurn(t, m, now)
	p := lastAnswer(*m).Provenance
	if p == nil {
		t.Fatal("a research answer has no provenance")
	}
	want := []string{
		"https://www.nps.gov/yose/planyourvisit/fees.htm",
		"https://www.nps.gov/yose/planyourvisit/camping.htm",
		"https://www.recreation.gov/camping/campgrounds/232447",
		"C:/Users/me/Documents/park_guide.pdf",
	}
	if strings.Join(p.sources, "|") != strings.Join(want, "|") {
		t.Errorf("sources = %v", p.sources)
	}
	got := screen(*m)
	if !strings.Contains(got, "ran on this PC · read 3 pages from nps.gov and 1 more site · 1 document") {
		t.Errorf("the provenance line is wrong:\n%s", got)
	}
	// Each source is a terminal hyperlink to the page itself.
	if styled := m.View(); !strings.Contains(styled, osc8Prefix+"https://www.nps.gov/yose/planyourvisit/fees.htm"+osc8Sep) {
		t.Errorf("sources are not links")
	}
}

func TestACloudTurnNamesTheProvider(t *testing.T) {
	for _, tc := range []struct{ backend, model, want string }{
		{"fireworks", "fireworks.kimi-k2", "ran on Fireworks AI"},
		{"amd", "amd.gpt-oss-120b", "ran on AMD LLM Gateway"},
		{"lemonade", "Gemma-4-E4B-it-GGUF", "ran on this PC"},
	} {
		m, now := frameChat(t, 100, 30)
		at(t, m, now, 0, event.CanonicalStatusEvent{
			Type: "status", ModelID: tc.model, ModelDisplay: tc.model,
			ModelBackend: tc.backend, ModelRemote: tc.backend != "lemonade",
		})
		startTurn(m, *now, "list the files here")
		at(t, m, now, time.Second, call("list_directory", `{"path":"."}`))
		at(t, m, now, time.Second, result("list_directory", `{"success":true}`))
		at(t, m, now, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "Three files."})
		if got := screen(*m); !strings.Contains(got, tc.want) {
			t.Errorf("%s: want %q in\n%s", tc.backend, tc.want, got)
		}
	}
}

func TestNothingIsAddedToAnAnswerWithoutTools(t *testing.T) {
	m, now := frameChat(t, 100, 20)
	startTurn(m, *now, "hello")
	at(t, m, now, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "Hi there."})
	if got := screen(*m); strings.Contains(got, "ran on") || strings.Contains(got, "Sources") {
		t.Errorf("a plain answer gained provenance:\n%s", got)
	}
}

func TestEveryLiveToolFailedSaysSo(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "what is the weather in Austin")
	at(t, m, now, time.Second, call("search_web", `{"query":"Austin weather"}`))
	at(t, m, now, time.Second, event.CanonicalToolResultEvent{Type: "tool_result", Tool: "search_web",
		Data: json.RawMessage(`{"success":false,"error":"network unreachable"}`)})
	at(t, m, now, time.Second, call("fetch_page", `{"url":"https://weather.gov/austin"}`))
	at(t, m, now, time.Second, event.CanonicalToolResultEvent{Type: "tool_result", Tool: "fetch_page",
		Data: json.RawMessage(`{"success":false,"error":"network unreachable"}`)})
	at(t, m, now, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "Usually hot."})
	got := screen(*m)
	if !strings.Contains(got, "[!] Couldn't get live data") {
		t.Errorf("an all-failed live lookup did not say so:\n%s", got)
	}
	if strings.Contains(got, "Sources") {
		t.Errorf("a failed fetch is listed as a source:\n%s", got)
	}

	// One success is live data: no warning.
	m2, now2 := frameChat(t, 100, 30)
	startTurn(m2, *now2, "what is the weather in Austin")
	at(t, m2, now2, time.Second, call("search_web", `{"query":"Austin weather"}`))
	at(t, m2, now2, time.Second, event.CanonicalToolResultEvent{Type: "tool_result", Tool: "search_web",
		Data: json.RawMessage(`{"success":false,"error":"network unreachable"}`)})
	at(t, m2, now2, time.Second, call("fetch_page", `{"url":"https://weather.gov/austin"}`))
	at(t, m2, now2, time.Second, result("fetch_page", `{"success":true}`))
	at(t, m2, now2, time.Second, event.CanonicalFinalEvent{Type: "final", Answer: "92°F."})
	if got := screen(*m2); strings.Contains(got, "Couldn't get live data") {
		t.Errorf("a turn that got a page still warns:\n%s", got)
	}
}

func TestFrameSources(t *testing.T) {
	for _, tc := range []struct {
		name string
		w, h int
	}{{"sources", 100, 30}, {"sources-40", 40, 34}} {
		t.Run(tc.name, func(t *testing.T) {
			m, now := frameChat(t, tc.w, tc.h)
			researchTurn(t, m, now)
			got := screen(*m)
			golden(t, tc.name, got)
			if nc := noColorView(t, *m, "ran on", "Sources", "nps.gov/"); nc != got {
				t.Errorf("the NO_COLOR frame differs:\n%s", nc)
			}
			for i, row := range strings.Split(got, "\n") {
				if w := ansi.StringWidth(row); w > tc.w {
					t.Errorf("row %d is %d columns wide: %q", i, w, row)
				}
			}
		})
	}
}
