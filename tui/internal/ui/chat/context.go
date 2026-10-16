// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"

	"github.com/amd/gaia/tui/internal/event"
)

// The context meter says how full the model's view is before older turns drop
// out of it: "ctx 64%" in the status bar, the breakdown on /context, and one
// line under the answer before a message would push turns out. Nothing is
// summarised or compacted — GAIA keeps long conversations in memory and
// documents, so this only makes the limit visible.

// contextNearFull is the fill at which the meter outranks the scroll hints.
const contextNearFull = 80

// contextMeter is the agent's latest context report.
type contextMeter struct {
	report event.ContextReport
	known  bool
	// warning waits for the turn to settle, so it lands under the answer.
	warning string
}

// capacity is what the model sees before history drops: the fixed prompt plus
// the history budget. The rest of the window is room for the answer.
func (c contextMeter) capacity() int {
	r := c.report
	return r.System + r.Tools + r.HistoryBudget
}

func (c contextMeter) used() int {
	r := c.report
	return r.System + r.Tools + r.Conversation + r.ToolResults
}

// percent is how full the view is, 0-100.
func (c contextMeter) percent() int {
	capacity := c.capacity()
	if !c.known || capacity <= 0 {
		return -1
	}
	return min(max(c.used()*100/capacity, 0), 100)
}

// takeContextReport records a report, and the warning it carries.
func (m *ChatModel) takeContextReport(r event.ContextReport) {
	m.ctxMeter.report = r
	m.ctxMeter.known = true
	m.ctxMeter.warning = ""
	if d := r.Dropping; d != nil && d.Turns > 0 {
		m.ctxMeter.warning = droppingWarning(*d)
	}
}

func droppingWarning(d event.ContextDropping) string {
	first := truncateRunes(clean(d.First), 48)
	what := "the oldest turn"
	if d.Turns > 1 {
		what = fmt.Sprintf("the oldest %d turns", d.Turns)
	}
	if first != "" {
		what += fmt.Sprintf(", from %q", first)
	}
	return "[!] Context is full: your next message pushes " + what +
		" out of the model's view. It stays saved, and GAIA can recall it if you ask."
}

// flushContextWarning puts a pending warning under the answer it followed.
func (m *ChatModel) flushContextWarning() {
	if m.ctxMeter.warning == "" {
		return
	}
	m.messages = append(m.messages, Message{Role: RoleStatus, Content: m.ctxMeter.warning})
	m.ctxMeter.warning = ""
}

// contextHint is the status-bar meter, ranked so a narrow bar drops it before
// the way out — unless the view is nearly full, when it is worth a column.
func (m ChatModel) contextHint() (hint, bool) {
	pct := m.ctxMeter.percent()
	if pct < 0 {
		return hint{}, false
	}
	if pct >= contextNearFull {
		return hint{text: fmt.Sprintf("ctx %d%% nearly full", pct), rank: rankContextFull}, true
	}
	return hint{text: fmt.Sprintf("ctx %d%%", pct), rank: rankContext}, true
}

// contextBreakdown is what /context prints.
func (m ChatModel) contextBreakdown() string {
	c := m.ctxMeter
	if c.percent() < 0 {
		return "No context report yet. It arrives with the agent's first answer."
	}
	r := c.report
	rows := [][2]string{
		{"instructions", thousands(r.System)},
		{"tool definitions", thousands(r.Tools)},
		{"conversation", thousands(r.Conversation)},
		{"files, pages and tool results", thousands(r.ToolResults)},
	}
	var b strings.Builder
	fmt.Fprintf(&b, "Context for your next message: %s of %s tokens (%d%%)\n",
		thousands(c.used()), thousands(c.capacity()), c.percent())
	for _, row := range rows {
		fmt.Fprintf(&b, "  %-30s %6s\n", row[0], row[1])
	}
	fmt.Fprintf(&b, "Past %s tokens of history, the oldest turns leave the model's view. "+
		"They stay saved, and GAIA can recall them. The %s-token window keeps the rest for the answer.",
		thousands(r.HistoryBudget), thousands(r.Window))
	return b.String()
}
