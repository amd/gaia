package chat

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// minTranscriptRowsWithTasks is what the transcript keeps before the task list
// gets a row: the list is a summary of the work, the transcript is the work.
const minTranscriptRowsWithTasks = 6

var (
	taskTitleStyle   = lipgloss.NewStyle().Foreground(theme.Text).Bold(true)
	taskDoneStyle    = lipgloss.NewStyle().Foreground(theme.Success)
	taskRunningStyle = lipgloss.NewStyle().Foreground(theme.Accent).Bold(true)
	taskCurrentStyle = lipgloss.NewStyle().Foreground(theme.Text).Bold(true)
	taskStaleStyle   = lipgloss.NewStyle().Foreground(theme.Warning)
)

// maxTaskItems caps what the card keeps from one event; the agent sends at
// most 12, so anything past this is a misbehaving peer.
const maxTaskItems = 50

// staleAfterSteps is how many tool calls without a list update make the card
// say the list is stale.
const staleAfterSteps = 4

// setTaskList replaces the pinned list; an empty one takes it down. Step text
// is cleaned here, once: it is model output and reaches the terminal verbatim.
func (m *ChatModel) setTaskList(e *event.CanonicalTaskListEvent) {
	if e != nil && len(e.Items) == 0 {
		e = nil
	}
	if e != nil {
		clean := *e
		n := len(e.Items)
		if n > maxTaskItems {
			n = maxTaskItems
		}
		clean.Items = make([]event.TaskListItem, n)
		for i := 0; i < n; i++ {
			item := e.Items[i]
			item.Content = oneLine(item.Content)
			if item.Status != event.TaskCompleted && item.Status != event.TaskInProgress {
				item.Status = event.TaskPending
			}
			clean.Items[i] = item
		}
		clean.CurrentAction = oneLine(e.CurrentAction)
		m.taskHidden = len(e.Items) - n
		e = &clean
	}
	m.taskList = e
	m.resize()
}

// oneLine strips escape sequences and control characters and folds whitespace.
func oneLine(s string) string {
	s = ansi.Strip(s)
	s = strings.Map(func(r rune) rune {
		if r < 0x20 || (r >= 0x7f && r < 0xa0) || (r >= 0x200b && r <= 0x200f) ||
			(r >= 0x202a && r <= 0x202e) || (r >= 0x2066 && r <= 0x2069) {
			return ' '
		}
		return r
	}, s)
	return strings.Join(strings.Fields(s), " ")
}

// renderTaskList draws the pinned list in at most maxRows rows, its own top
// divider included, or "" when there is no list or no room for one.
//
// The marks are ASCII, so the list reads the same without colour and without a
// symbol font. While a turn runs every step shows; between turns it folds to a
// single line. The live region, not this card, owns the current action and its
// clock — one event on screen twice with two clocks is the defect it fixed.
func (m ChatModel) renderTaskList(maxRows int) string {
	tl := m.taskList
	if tl == nil || maxRows < 2 || m.width <= 0 {
		return ""
	}
	divider := dividerStyle.Render(strings.Repeat("─", m.width))
	done, current := 0, -1
	for i, item := range tl.Items {
		switch item.Status {
		case event.TaskCompleted:
			done++
		case event.TaskInProgress:
			if current < 0 {
				current = i
			}
		}
	}
	title := "Tasks"
	if tl.Source == "plan" {
		title = "Plan"
	}
	total := len(tl.Items) + m.taskHidden
	count := fmt.Sprintf("%d of %d", done, total)
	allDone := done == len(tl.Items) && m.taskHidden == 0
	switch {
	case allDone && m.streaming:
		// Never claim "done" while the agent is still working.
		count += " · still working"
	case allDone:
		count += " done"
	}
	head := " " + taskTitleStyle.Render(title) + " " + activityStyle.Render(count)
	if m.streaming && tl.StepsSinceUpdate >= staleAfterSteps {
		head += " " + taskStaleStyle.Render(fmt.Sprintf("· not updated for %d steps", tl.StepsSinceUpdate))
	}

	available := maxRows - 2 // divider and head
	if !m.streaming || available < 1 {
		if !allDone {
			next := current
			if next < 0 {
				next = firstPending(tl.Items)
			}
			if next >= 0 {
				head += activityStyle.Render(" · next: " + tl.Items[next].Content)
			}
		}
		return divider + "\n" + m.fitTaskLine(head)
	}

	rows := []string{divider, m.fitTaskLine(head)}
	start, end := taskWindow(len(tl.Items), current, available, m.taskHidden)
	for i := start; i < end; i++ {
		rows = append(rows, m.renderTaskItem(tl.Items[i]))
	}
	if hidden := total - (end - start); hidden > 0 {
		rows = append(rows, activityStyle.Render(fmt.Sprintf("   … %d more", hidden)))
	}
	return strings.Join(rows, "\n")
}

// taskWindow picks which of n steps fit in *rows*, keeping the running one in
// view. When any step is left out — here, or already (*dropped*) — it saves a
// row for the "… N more" line.
func taskWindow(n, current, rows, dropped int) (int, int) {
	if n <= rows && (dropped == 0 || n < rows) {
		return 0, n
	}
	shown := rows - 1
	if shown < 1 {
		shown = 1
	}
	start := current - 1
	if start > n-shown {
		start = n - shown
	}
	if start < 0 {
		start = 0
	}
	return start, start + shown
}

func firstPending(items []event.TaskListItem) int {
	for i, item := range items {
		if item.Status == event.TaskPending {
			return i
		}
	}
	return -1
}

func (m ChatModel) renderTaskItem(item event.TaskListItem) string {
	var mark, text string
	switch item.Status {
	case event.TaskCompleted:
		mark, text = taskDoneStyle.Render("[x]"), activityStyle.Render(item.Content)
	case event.TaskInProgress:
		mark, text = taskRunningStyle.Render("[>]"), taskCurrentStyle.Render(item.Content)
	default:
		mark, text = activityStyle.Render("[ ]"), item.Content
	}
	took := ""
	if item.Status == event.TaskCompleted && item.StartedAt > 0 && item.CompletedAt >= item.StartedAt {
		took = taskElapsed(item.CompletedAt - item.StartedAt)
	}
	line := "  " + mark + " " + text
	if took == "" {
		return m.fitTaskLine(line)
	}
	// The time keeps its place at the right edge; the step text gives way.
	room := m.width - lipgloss.Width(took) - 2
	if room < 8 {
		return m.fitTaskLine(line)
	}
	line = ansi.Truncate(line, room, "…")
	gap := m.width - lipgloss.Width(line) - lipgloss.Width(took) - 1
	if gap < 1 {
		gap = 1
	}
	return line + strings.Repeat(" ", gap) + activityStyle.Render(took)
}

func (m ChatModel) fitTaskLine(line string) string {
	return ansi.Truncate(line, m.width, "…")
}

// taskElapsed reads 12s, 2m 05s or 1h 04m.
func taskElapsed(seconds float64) string {
	s := int(seconds)
	if s < 60 {
		return fmt.Sprintf("%ds", s)
	}
	if s < 3600 {
		return fmt.Sprintf("%dm %02ds", s/60, s%60)
	}
	return fmt.Sprintf("%dh %02dm", s/3600, (s/60)%60)
}
