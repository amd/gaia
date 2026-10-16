// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

func contextEvent(r event.ContextReport) event.CanonicalStatusEvent {
	return event.CanonicalStatusEvent{Type: "status", Context: &r}
}

// A 64K window: 8K of prompt and tools, 24K of history before turns drop.
func report(conversation, results int) event.ContextReport {
	return event.ContextReport{
		Window: 65536, System: 3000, Tools: 5000,
		Conversation: conversation, ToolResults: results, HistoryBudget: 24000,
	}
}

func TestContextMeterMovesWhenALongFileIsRead(t *testing.T) {
	m, now := frameChat(t, 120, 24)
	if _, ok := m.contextHint(); ok {
		t.Fatal("a meter before any report would be a guess")
	}
	startTurn(m, *now, "hi")
	at(t, m, now, time.Second, contextEvent(report(1000, 0)), event.CanonicalFinalEvent{Type: "final", Answer: "Hello."})
	before, _ := m.contextHint()
	startTurn(m, *now, "read big.txt")
	at(t, m, now, time.Second, contextEvent(report(1500, 12000)), event.CanonicalFinalEvent{Type: "final", Answer: "Read it."})
	after, _ := m.contextHint()
	if before.text != "ctx 28%" || after.text != "ctx 67%" {
		t.Errorf("meter read %q then %q, want ctx 28%% then ctx 67%%", before.text, after.text)
	}
	if !strings.Contains(screen(*m), "ctx 67%") {
		t.Errorf("the meter is not in the status bar:\n%s", screen(*m))
	}
}

func TestContextNearlyFullSaysSoInWords(t *testing.T) {
	m, _ := frameChat(t, 120, 24)
	m.takeContextReport(report(15000, 6000))
	h, _ := m.contextHint()
	if h.text != "ctx 90% nearly full" || h.rank <= rankAffordance {
		t.Errorf("near-full meter is %+v", h)
	}
}

func TestContextWarningLandsUnderTheAnswerBeforeTurnsDrop(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "and one more thing")
	r := report(23000, 0)
	r.Dropping = &event.ContextDropping{Turns: 3, First: "Review the 8 contracts in ./contracts"}
	at(t, m, now, time.Second, contextEvent(r), event.CanonicalFinalEvent{Type: "final", Answer: "Done."})
	last := m.messages[len(m.messages)-1]
	if last.Role != RoleStatus || !strings.Contains(last.Content, "the oldest 3 turns") ||
		!strings.Contains(last.Content, "Review the 8 contracts") || !strings.HasPrefix(last.Content, "[!]") {
		t.Errorf("want the warning after the answer, got %+v", last)
	}
	if prev := m.messages[len(m.messages)-2]; prev.Role != RoleAssistant {
		t.Errorf("the warning should follow the answer, it follows %s", prev.Role)
	}
}

func TestContextBreakdownNamesEachPart(t *testing.T) {
	m, _ := frameChat(t, 100, 30)
	if got := m.contextBreakdown(); !strings.Contains(got, "No context report yet") {
		t.Errorf("before a report: %q", got)
	}
	m.takeContextReport(report(4000, 9000))
	got := m.contextBreakdown()
	for _, want := range []string{"21.0k of 32.0k tokens (65%)", "instructions", "tool definitions", "conversation", "files, pages and tool results", "9.0k", "Past 24.0k tokens"} {
		if !strings.Contains(got, want) {
			t.Errorf("breakdown lacks %q:\n%s", want, got)
		}
	}
	mm, _ := m.submit("/context")
	if c := mm.(ChatModel).messages; len(c) == 0 || c[len(c)-1].Content != got {
		t.Errorf("/context did not print the breakdown")
	}
}

func TestContextMeterDropsOutOnNarrowTerminals(t *testing.T) {
	m, _ := frameChat(t, 40, 24)
	m.takeContextReport(report(4000, 0))
	got := screen(*m)
	if strings.Contains(got, "ctx") {
		t.Errorf("a 40-column bar should keep the way out, not the meter:\n%s", got)
	}
	if !strings.Contains(got, "Ctrl+C quit") {
		t.Errorf("the way out was dropped:\n%s", got)
	}
}

func TestClearResetsTheMeter(t *testing.T) {
	m, _ := frameChat(t, 100, 30)
	m.takeContextReport(report(4000, 0))
	mm, _ := m.handleConversationCleared(conversationClearedMsg{turnSeq: m.turnSeq})
	if _, ok := mm.(ChatModel).contextHint(); ok {
		t.Error("the meter outlived /clear")
	}
}

func TestFrameContextWarning(t *testing.T) {
	for _, tc := range []struct {
		name string
		w, h int
	}{{"context-warning", 100, 20}, {"context-warning-40", 40, 24}} {
		t.Run(tc.name, func(t *testing.T) {
			m, now := frameChat(t, tc.w, tc.h)
			startTurn(m, *now, "Now compare it with last quarter's numbers")
			r := report(15500, 6000)
			r.Dropping = &event.ContextDropping{Turns: 2, First: "Review the 8 contracts in ./contracts and build a risk register"}
			at(t, m, now, 4*time.Second, contextEvent(r), event.CanonicalFinalEvent{Type: "final", Answer: "Revenue is up 4% on last quarter."})
			got := screen(*m)
			golden(t, tc.name, got)
			if nc := noColorView(t, *m, "[!]", "ctx "); nc != got {
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
