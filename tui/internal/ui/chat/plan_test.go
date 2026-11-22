// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"

	"github.com/amd/gaia/tui/internal/event"
)

func planEvent(current int, steps ...event.PlanStep) event.CanonicalStatusEvent {
	return event.CanonicalStatusEvent{Type: "status", Plan: &event.PlanState{Steps: steps, CurrentStep: current}}
}

var memoPlan = []event.PlanStep{
	{Tool: "read_file", Args: json.RawMessage(`{"file_path":"term_sheet.pdf"}`)},
	{Tool: "search_web", Args: json.RawMessage(`{"query":"seed round terms 2026"}`)},
	{Tool: "write_file", Description: "Write the 2-page memo", Args: json.RawMessage(`{"file_path":"memo.md","content":"x"}`)},
}

// noColorView is the frame as drawn with no colour at all — what NO_COLOR
// gets. The rows carrying any of markers must have no SGR sequence, so their
// state has to be in the text.
func noColorView(t *testing.T, m ChatModel, markers ...string) string {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.Ascii)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
	m.motion = motionFrom(motionProbe{
		getenv:  func(k string) string { return map[string]string{"NO_COLOR": "1"}[k] },
		isTTY:   func() bool { return true },
		profile: func() termenv.Profile { return termenv.Ascii },
		config:  func() []byte { return nil },
	})
	m.updateViewport()
	sgr := regexp.MustCompile(`\x1b\[[0-9;]*m`)
	rows := strings.Split(m.View(), "\n")
	for i, r := range rows {
		plain := strings.TrimRight(ansi.Strip(r), " ")
		for _, mark := range markers {
			if strings.Contains(plain, mark) && sgr.MatchString(r) {
				t.Errorf("row %q is coloured under NO_COLOR", plain)
			}
		}
		rows[i] = plain
	}
	return strings.Join(rows, "\n") + "\n"
}

func TestPlanChecklistMarksEachStep(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Compare this term sheet against current seed terms")
	at(t, m, now, time.Second, planEvent(0, memoPlan...), call("read_file", `{"file_path":"term_sheet.pdf"}`))

	states := func() string {
		var s []string
		for i := range m.plan.labels {
			s = append(s, m.plan.planState(i))
		}
		return strings.Join(s, ",")
	}
	if got := states(); got != "running,pending,pending" {
		t.Fatalf("at the start: %s", got)
	}
	at(t, m, now, time.Second, result("read_file", `{"success":true}`))
	if got := states(); got != "done,pending,pending" {
		t.Errorf("after the first step's result: %s", got)
	}
	at(t, m, now, time.Second, planEvent(1, memoPlan...), call("search_web", `{"query":"seed round terms 2026"}`))
	if got := states(); got != "done,running,pending" {
		t.Errorf("after the plan moved on: %s", got)
	}
	// A failure leaves the step running: the agent retries it.
	at(t, m, now, time.Second, result("search_web", `{"success":false,"error":"timeout"}`))
	if got := states(); got != "done,running,pending" {
		t.Errorf("a failed step was ticked: %s", got)
	}
}

func TestPlanChangeReplacesTheList(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Do the thing")
	at(t, m, now, time.Second, planEvent(1, memoPlan...))
	at(t, m, now, time.Second, planEvent(0,
		event.PlanStep{Description: "Ask for the missing term sheet"},
		event.PlanStep{Description: "Summarise what is known"}))
	got := screen(*m)
	if strings.Contains(got, "memo") || strings.Count(got, "Plan ·") != 1 {
		t.Errorf("a new plan should replace the old one, not add to it:\n%s", got)
	}
	if !strings.Contains(got, "Plan · 0 of 2 done") {
		t.Errorf("the new plan's header is missing:\n%s", got)
	}
}

func TestPlanFromLegacyEvent(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Do the thing")
	at(t, m, now, time.Second, event.PlanEvent{Type: "plan", Steps: json.RawMessage(`["Read the file","Write the summary"]`), CurrentStep: 1})
	if got := m.plan.labels; len(got) != 2 || got[1] != "Write the summary" || m.plan.current != 1 {
		t.Errorf("legacy plan read as %+v", m.plan)
	}
}

func TestOneStepIsNotAPlan(t *testing.T) {
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Read it")
	at(t, m, now, time.Second, planEvent(0, memoPlan[0]))
	if strings.Contains(screen(*m), "Plan ·") {
		t.Errorf("a one-step plan drew a checklist")
	}
}

func TestPlanLongListKeepsTheRunningStepInView(t *testing.T) {
	var steps []event.PlanStep
	for i := 0; i < 12; i++ {
		steps = append(steps, event.PlanStep{Description: "Reconcile batch " + string(rune('A'+i))})
	}
	m, now := frameChat(t, 100, 30)
	startTurn(m, *now, "Reconcile")
	at(t, m, now, time.Second, planEvent(8, steps...))
	got := screen(*m)
	if !strings.Contains(got, "▸ Reconcile batch I") || !strings.Contains(got, "… ") {
		t.Errorf("the running step or the fold is missing:\n%s", got)
	}
	if strings.Contains(got, "batch A") {
		t.Errorf("a 12-step plan should not show its first step while on step 9:\n%s", got)
	}
}

func TestFramePlanMidTurn(t *testing.T) {
	for _, tc := range []struct {
		name string
		w, h int
	}{{"plan-running", 100, 24}, {"plan-running-40", 40, 24}} {
		t.Run(tc.name, func(t *testing.T) {
			m, now := frameChat(t, tc.w, tc.h)
			startTurn(m, *now, "Compare this term sheet against current seed terms and write me a 2-page memo")
			at(t, m, now, time.Second, planEvent(0, memoPlan...), call("read_file", `{"file_path":"term_sheet.pdf"}`))
			at(t, m, now, 3*time.Second, result("read_file", `{"success":true}`))
			at(t, m, now, time.Second, planEvent(1, memoPlan...), call("search_web", `{"query":"seed round terms 2026"}`))
			*now = now.Add(7 * time.Second)
			got := screen(*m)
			golden(t, tc.name, got)
			if nc := noColorView(t, *m, "Plan ·", "✓", "▸", "○"); nc != got {
				t.Errorf("the NO_COLOR frame differs:\n%s", nc)
			}
			for _, want := range []string{"Plan · 1 of 3 done", "✓ Read term_sheet.pdf", "▸ Search the web", "○ Write the 2-page memo"} {
				if !strings.Contains(got, want) && tc.w == 100 {
					t.Errorf("missing %q:\n%s", want, got)
				}
			}
			for i, row := range strings.Split(got, "\n") {
				if w := ansi.StringWidth(row); w > tc.w {
					t.Errorf("row %d is %d columns wide: %q", i, w, row)
				}
			}
		})
	}
}
