// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"sort"
	"strconv"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Delegated work is one step in the log like any other tool: progress rides on
// the status line while it runs, its `└` line is the result's summary, and
// Ctrl+O lists each helper under it. Recognised by the result's shape (a
// `summary` and a `helpers` list), never by the narration, which differs
// between agents.

// phaseHelpers is the status phase a running delegation reports progress on.
const phaseHelpers = "helpers"

// maxHelperRows is the most helpers one step lists; a shared-out task can run
// more, and those are counted on a closing "… N more" line.
const maxHelperRows = 16

// foldedHelpersHint points at the per-helper lines; the status bar's wording.
const foldedHelpersHint = " · Ctrl+O details"

const (
	helperOK      = "ok"
	helperPartial = "partial"
	helperFailed  = "failed"
)

const (
	glyphHelperOK      = "✓"
	glyphHelperPartial = "~"
	glyphHelperFailed  = "✗"
)

var (
	helperOKStyle   = lipgloss.NewStyle().Foreground(theme.Success)
	helperTextStyle = lipgloss.NewStyle().Foreground(theme.Text)
	helperWarnStyle = lipgloss.NewStyle().Foreground(theme.Warning)
)

// helperOutcome is one helper's result as the work log shows it.
type helperOutcome struct {
	ID         int
	Task       string
	Status     string
	Reason     string
	Conclusion string
}

// delegation is a delegate_task result reduced to what a person reads.
type delegation struct {
	status  string
	summary string
	helpers []helperOutcome
	// more counts helpers past maxHelperRows, named on a closing line.
	more int
}

// delegationOf reads a delegation result. ok is false for anything else,
// including a result cut down on the wire, which then has no helper list.
func delegationOf(data json.RawMessage) (delegation, bool) {
	var raw struct {
		Status  string `json:"status"`
		Summary string `json:"summary"`
		Helpers *[]struct {
			ID         int    `json:"id"`
			Task       string `json:"task"`
			Status     string `json:"status"`
			Reason     string `json:"reason"`
			Conclusion string `json:"conclusion"`
		} `json:"helpers"`
	}
	if len(data) == 0 || json.Unmarshal(data, &raw) != nil || raw.Helpers == nil {
		return delegation{}, false
	}
	summary := firstLine(raw.Summary)
	if summary == "" {
		return delegation{}, false
	}
	d := delegation{status: raw.Status, summary: truncateRunes(summary, detailMax)}
	for _, h := range *raw.Helpers {
		status := h.Status
		if status != helperOK && status != helperPartial {
			status = helperFailed
		}
		d.helpers = append(d.helpers, helperOutcome{
			ID:         h.ID,
			Task:       truncateRunes(clean(h.Task), widestLogMeasure),
			Status:     status,
			Reason:     truncateRunes(clean(h.Reason), 80),
			Conclusion: truncateRunes(firstTextLine(h.Conclusion), widestLogMeasure),
		})
	}
	sort.SliceStable(d.helpers, func(i, j int) bool { return d.helpers[i].ID < d.helpers[j].ID })
	if len(d.helpers) > maxHelperRows {
		d.more = len(d.helpers) - maxHelperRows
		d.helpers = d.helpers[:maxHelperRows]
	}
	return d, true
}

// firstTextLine is the first line of s that has any text on it.
func firstTextLine(s string) string {
	for _, line := range strings.FieldsFunc(s, func(r rune) bool { return r == '\n' || r == '\r' }) {
		if c := clean(line); c != "" {
			return c
		}
	}
	return ""
}

// closeDelegation ends the open step with a delegation's outcome and returns
// its index. Only a result with nothing usable in it counts as a failed step.
func (m *ChatModel) closeDelegation(tool string, d delegation) int {
	usable := d.status != "error"
	detail := d.summary
	if !usable {
		detail = markFailed(detail, true)
	}
	at := m.setOpenToolOutcome(tool, usable, detail)
	item := &m.activity[at]
	item.Progress = ""
	item.Helpers = d.helpers
	item.MoreHelpers = d.more
	item.Partial = usable && (d.status == helperPartial || anyHelperShort(d.helpers))
	return at
}

func anyHelperShort(helpers []helperOutcome) bool {
	for _, h := range helpers {
		if h.Status != helperOK {
			return true
		}
	}
	return false
}

// setHelperProgress puts a helpers-phase message on the newest open step.
// False when no step is open to carry it.
func (m *ChatModel) setHelperProgress(message string) bool {
	msg := truncateRunes(clean(message), narrationMax)
	if msg == "" {
		return true
	}
	for i := len(m.activity) - 1; i >= 0; i-- {
		item := &m.activity[i]
		if item.Kind == "tool" && !item.Done {
			item.Progress = msg
			return true
		}
	}
	return false
}

// helperProgress turns "3 of 4 helpers done, 1 failed" into the status
// line's suffix, "3 of 4 done, 1 failed": the step already says "helpers".
func helperProgress(message string) string {
	for _, word := range []string{" helpers done", " helper done"} {
		message = strings.Replace(message, word, " done", 1)
	}
	return message
}

// helperReason is why a helper stopped, in plain words.
func helperReason(reason string) string {
	switch reason {
	case "step_limit":
		return "out of steps"
	case "loop":
		return "kept repeating a step"
	case "empty":
		return "no answer"
	case "error":
		return "the model server failed"
	case "timeout":
		return "no answer in time"
	case "cancelled":
		return "stopped"
	}
	return strings.ReplaceAll(reason, "_", " ")
}

// helperOutcomeText is what a helper line says after the task.
func helperOutcomeText(h helperOutcome) string {
	why := helperReason(h.Reason)
	switch h.Status {
	case helperOK:
		return h.Conclusion
	case helperPartial:
		head := "stopped short"
		if why != "" {
			head += " (" + why + ")"
		}
		if h.Conclusion == "" {
			return head
		}
		return head + ": " + h.Conclusion
	}
	if why == "" {
		return "failed"
	}
	return "failed: " + why
}

// renderHelperLines draws one row per helper: mark, number, task, outcome.
// Each row is clipped, never wrapped. maxRows of 0 means every helper.
func (m ChatModel) renderHelperLines(item ActivityItem, maxRows int) []string {
	helpers := item.Helpers
	hidden := item.MoreHelpers
	if maxRows > 0 && len(helpers)+min(hidden, 1) > maxRows {
		keep := maxRows - 1
		hidden += len(helpers) - keep
		helpers = helpers[:keep]
	}
	const indent = "       "
	// Three columns deeper than a step's own rows.
	avail := m.logWidth() - 3

	idWidth, longestTask, longestOutcome := 1, 1, 1
	for _, h := range helpers {
		idWidth = max(idWidth, len(strconv.Itoa(h.ID)))
		longestTask = max(longestTask, displayWidth(h.Task))
		longestOutcome = max(longestOutcome, displayWidth(helperOutcomeText(h)))
	}
	// mark, space, number, two spaces, task, three spaces, outcome
	rest := avail - (idWidth + 7)
	// Two fifths for the task, plus whatever no outcome needs.
	taskWidth := max(min(longestTask, max(rest*2/5, rest-longestOutcome)), 1)
	outcomeWidth := max(rest-taskWidth, 1)

	lines := make([]string, 0, len(helpers)+1)
	for _, h := range helpers {
		glyph, glyphStyle, outcomeStyle := glyphHelperOK, helperOKStyle, helperTextStyle
		switch h.Status {
		case helperPartial:
			glyph, glyphStyle, outcomeStyle = glyphHelperPartial, helperWarnStyle, helperWarnStyle
		case helperFailed:
			glyph, glyphStyle, outcomeStyle = glyphHelperFailed, failStyle, failStyle
		}
		id := strconv.Itoa(h.ID)
		task := truncateRunes(h.Task, taskWidth)
		line := indent + glyphStyle.Render(glyph) + " " +
			activityStyle.Render(strings.Repeat(" ", idWidth-len(id))+id+"  "+task)
		if outcome := truncateRunes(helperOutcomeText(h), outcomeWidth); outcome != "" {
			line += strings.Repeat(" ", taskWidth-displayWidth(task)+3) + outcomeStyle.Render(outcome)
		}
		lines = append(lines, line)
	}
	if hidden > 0 {
		lines = append(lines, indent+activityStyle.Render(
			truncateRunes("… "+strconv.Itoa(hidden)+" more", avail)))
	}
	return lines
}
