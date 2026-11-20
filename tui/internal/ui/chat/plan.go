// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// The plan, when the agent made one, is a checklist above the running step:
//
//	Plan · 1 of 3 done
//	  ✓ Read the term sheet
//	  ▸ Search the web for "seed round terms 2026"
//	  ○ Write the 2-page memo
//
// It is redrawn in place as the step moves, and replaced when the agent plans
// again. The glyph carries the state, so it reads the same without colour.

const (
	glyphPlanDone    = "✓"
	glyphPlanRunning = "▸"
	glyphPlanPending = "○"
)

// planMaxRows caps the checklist, header included, so a 20-step plan cannot
// take over the live region.
const planMaxRows = 7

var (
	planHeadStyle    = lipgloss.NewStyle().Foreground(theme.Dim)
	planDoneStyle    = lipgloss.NewStyle().Foreground(theme.Success)
	planRunningStyle = lipgloss.NewStyle().Foreground(theme.Text).Bold(true)
	planPendingStyle = lipgloss.NewStyle().Foreground(theme.Dim)
)

// livePlan is the running turn's plan. done counts the steps known finished;
// it can run one ahead of current, between a step's result and the agent
// moving on.
type livePlan struct {
	labels  []string
	current int
	done    int
	// tools is each step's tool, to recognise the result that finishes it.
	tools []string
}

// active reports whether there is a plan worth drawing: one step is a single
// action, not a plan.
func (p livePlan) active() bool { return len(p.labels) > 1 }

// planFromWire turns the agent's steps into checklist lines.
func planFromWire(steps []event.PlanStep, current int) livePlan {
	p := livePlan{current: current}
	for _, s := range steps {
		label := cleanDescription(s.Description)
		if label == "" && s.Tool != "" {
			label = describeCall(s.Tool, s.Args, "").Derived
		}
		if label == "" {
			label = "Next step"
		}
		p.labels = append(p.labels, label)
		p.tools = append(p.tools, s.Tool)
	}
	return p
}

// legacyPlanSteps reads the subprocess vocabulary's plan: structured items when
// the agent sent them, else the flat step strings.
func legacyPlanSteps(e event.PlanEvent) []event.PlanStep {
	if len(e.Items) > 0 {
		return e.Items
	}
	var flat []string
	if err := json.Unmarshal(e.Steps, &flat); err != nil {
		return nil
	}
	steps := make([]event.PlanStep, len(flat))
	for i, s := range flat {
		steps[i] = event.PlanStep{Description: s}
	}
	return steps
}

// updatePlan takes the agent's latest plan: a new list replaces the old one,
// the same list only moves its current step.
func (m *ChatModel) updatePlan(next livePlan) {
	if sameSteps(m.plan.labels, next.labels) {
		m.plan.current = next.current
		m.plan.done = max(m.plan.done, next.current)
		return
	}
	next.done = next.current
	m.plan = next
}

func sameSteps(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// planToolFinished ticks the running step when its own tool succeeded — the
// rule the agent advances by. A failure leaves it running: the agent retries.
func (m *ChatModel) planToolFinished(tool string, success bool) {
	p := &m.plan
	if !success || !p.active() || p.current < 0 || p.current >= len(p.tools) || p.done > p.current {
		return
	}
	if want := p.tools[p.current]; want != "" && want != tool {
		return
	}
	p.done = p.current + 1
}

// planState is "done", "running" or "pending" for step i.
func (p livePlan) planState(i int) string {
	switch {
	case i < p.done:
		return "done"
	case i == p.current:
		return "running"
	}
	return "pending"
}

// renderPlan is the checklist at this width, or nothing when there is no plan.
func (m ChatModel) renderPlan(width int) []string {
	p := m.plan
	if !p.active() {
		return nil
	}
	n := len(p.labels)
	done := min(p.done, n)
	rows := []string{"  " + planHeadStyle.Render(truncateRunes(fmt.Sprintf("Plan · %d of %d done", done, n), max(width-2, 1)))}

	room := planMaxRows - 1
	start, end := 0, n
	if n > room {
		start = min(max(p.current-1, 0), n-room)
		end = start + room
	}
	for i := start; i < end; i++ {
		if i == end-1 && end < n {
			rows = append(rows, "    "+planPendingStyle.Render(fmt.Sprintf("… %d more", n-end+1)))
			break
		}
		rows = append(rows, planRow(p.planState(i), p.labels[i], width))
	}
	return rows
}

func planRow(state, label string, width int) string {
	glyph, style := glyphPlanPending, planPendingStyle
	switch state {
	case "done":
		glyph, style = glyphPlanDone, planDoneStyle
	case "running":
		glyph, style = glyphPlanRunning, planRunningStyle
	}
	label = truncateRunes(label, max(width-6, 1))
	if state == "done" {
		return "    " + style.Render(glyph) + " " + planPendingStyle.Render(label)
	}
	return "    " + style.Render(glyph) + " " + style.Render(label)
}
