package chat

import (
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

const t0 = 1_800_000_000.0

func threeSteps() event.CanonicalTaskListEvent {
	return event.CanonicalTaskListEvent{
		Type: "task_list",
		Items: []event.TaskListItem{
			{ID: 1, Content: "Read the config", Status: event.TaskCompleted, StartedAt: t0, CompletedAt: t0 + 12},
			{ID: 2, Content: "Fix the off-by-one in the pager", Status: event.TaskInProgress, StartedAt: t0 + 12},
			{ID: 3, Content: "Run the tests", Status: event.TaskPending},
		},
		Source:          "agent",
		CurrentAction:   "Running command: go test ./...",
		ActionStartedAt: t0 + 20,
		UpdatedAt:       t0 + 20,
	}
}

func cardLines(m ChatModel) []string {
	out := ansi.Strip(m.renderTaskList(m.taskRows))
	lines := strings.Split(out, "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " ")
	}
	return lines
}

func assertFrame(t *testing.T, got []string, want string) {
	t.Helper()
	if g := strings.Join(got, "\n"); g != want {
		t.Errorf("task list drifted from the golden:\n%s\n--- want ---\n%s", g, want)
	}
}

func TestTaskListPinsEveryStepWhileTheAgentWorks(t *testing.T) {
	m := feed(t, newTestChat(t), threeSteps())

	assertFrame(t, cardLines(m), strings.Join([]string{
		strings.Repeat("─", 80),
		" Tasks 1 of 3",
		"  [x] Read the config" + strings.Repeat(" ", 55) + "12s",
		"  [>] Fix the off-by-one in the pager",
		"  [ ] Run the tests",
	}, "\n"))
}

func TestTaskListFitsFortyColumns(t *testing.T) {
	m := newTestChat(t)
	m.width = 40
	m.resize()
	m = feed(t, m, threeSteps())

	lines := cardLines(m)
	assertFrame(t, lines, strings.Join([]string{
		strings.Repeat("─", 40),
		" Tasks 1 of 3",
		"  [x] Read the config" + strings.Repeat(" ", 15) + "12s",
		"  [>] Fix the off-by-one in the pager",
		"  [ ] Run the tests",
	}, "\n"))
	for _, l := range strings.Split(m.renderTaskList(m.taskRows), "\n") {
		if w := lipgloss.Width(l); w > 40 {
			t.Errorf("a row is %d cells wide on a 40-column terminal: %q", w, ansi.Strip(l))
		}
	}
}

func TestTaskListFoldsToOneLineBetweenTurns(t *testing.T) {
	m := feed(t, newTestChat(t), threeSteps())
	m.streaming = false
	m.resize()

	assertFrame(t, cardLines(m), strings.Join([]string{
		strings.Repeat("─", 80),
		" Tasks 1 of 3 · next: Fix the off-by-one in the pager",
	}, "\n"))

	done := threeSteps()
	for i := range done.Items {
		done.Items[i].Status = event.TaskCompleted
	}
	m = feed(t, m, done)
	if got := cardLines(m)[1]; got != " Tasks 3 of 3 done" {
		t.Errorf("finished list reads %q", got)
	}
}

// Readable with no colour and no symbol font: the state is in the ASCII mark.
func TestTaskListMarksNeedNoColourOrSymbolFont(t *testing.T) {
	m := feed(t, newTestChat(t), threeSteps())
	out := m.renderTaskList(m.taskRows)
	for _, r := range ansi.Strip(out) {
		if r > 0x7f && r != '─' && r != '…' && r != '·' {
			t.Errorf("task list depends on %q rendering", r)
		}
	}
}

func TestTaskListTakesItsRowsFromTheTranscript(t *testing.T) {
	m := newTestChat(t)
	before := m.viewport.Height
	m = feed(t, m, threeSteps())
	if m.taskRows != 5 {
		t.Fatalf("taskRows = %d, want 5 (divider, head, three steps)", m.taskRows)
	}
	if m.viewport.Height != before-m.taskRows {
		t.Errorf("transcript is %d rows, want %d", m.viewport.Height, before-m.taskRows)
	}
	if h := lipgloss.Height(m.View()); h != m.height {
		t.Errorf("frame is %d rows on a %d-row terminal", h, m.height)
	}
}

func TestALongListKeepsTheRunningStepInView(t *testing.T) {
	m := newTestChat(t)
	m.height = 16
	m.resize()
	long := threeSteps()
	long.Items = nil
	for i := 1; i <= 10; i++ {
		status := event.TaskPending
		if i < 8 {
			status = event.TaskCompleted
		}
		if i == 8 {
			status = event.TaskInProgress
		}
		long.Items = append(long.Items, event.TaskListItem{ID: i, Content: "step " + string(rune('0'+i%10)), Status: status})
	}
	m = feed(t, m, long)

	out := strings.Join(cardLines(m), "\n")
	if !strings.Contains(out, "[>] step 8") {
		t.Errorf("the running step scrolled out of the card:\n%s", out)
	}
	if !strings.Contains(out, "more") {
		t.Errorf("hidden steps are not counted:\n%s", out)
	}
	if m.viewport.Height < minTranscriptRowsWithTasks {
		t.Errorf("transcript shrank to %d rows", m.viewport.Height)
	}
	if h := lipgloss.Height(m.View()); h != m.height {
		t.Errorf("frame is %d rows on a %d-row terminal", h, m.height)
	}
}

func TestAnEmptyListTakesTheCardDown(t *testing.T) {
	m := feed(t, newTestChat(t), threeSteps())
	empty := threeSteps()
	empty.Items = nil
	m = feed(t, m, empty)
	if m.taskList != nil || m.taskRows != 0 {
		t.Errorf("card still up: taskList=%v rows=%d", m.taskList, m.taskRows)
	}
}

func TestClearingTheConversationClearsTheList(t *testing.T) {
	m := feed(t, newTestChat(t), threeSteps())
	updated, _ := m.handleConversationCleared(conversationClearedMsg{turnSeq: m.turnSeq})
	if updated.(ChatModel).taskList != nil {
		t.Error("the list survived /clear")
	}
}

func TestTaskElapsed(t *testing.T) {
	for in, want := range map[float64]string{0: "0s", 12.7: "12s", 125: "2m 05s", 3840: "1h 04m"} {
		if got := taskElapsed(in); got != want {
			t.Errorf("taskElapsed(%v) = %q, want %q", in, got, want)
		}
	}
}
