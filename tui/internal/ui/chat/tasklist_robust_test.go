package chat

import (
	"fmt"
	"strings"
	"testing"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"

	"github.com/amd/gaia/tui/internal/event"
)

func listOf(statuses ...string) event.CanonicalTaskListEvent {
	e := event.CanonicalTaskListEvent{Type: "task_list"}
	for i, s := range statuses {
		e.Items = append(e.Items, event.TaskListItem{ID: i + 1, Content: fmt.Sprintf("step %d", i+1), Status: s})
	}
	return e
}

func plain(m ChatModel) string { return ansi.Strip(m.renderTaskList(m.taskRows)) }

// Step text is model output; it must never move the cursor, clear the screen,
// write the clipboard or flip text direction on the user's terminal.
func TestTaskListStripsEscapesFromStepText(t *testing.T) {
	hostile := listOf(event.TaskInProgress, event.TaskPending)
	hostile.Items[0].Content = "fix \x1b[2J\x1b[H\x1b]52;c;ZXZpbA==\x07bug"
	hostile.Items[1].Content = "rtl‮evil​\r\nnext"
	hostile.CurrentAction = "\x1b[31mred\x1b[0m"
	m := feed(t, newTestChat(t), hostile)

	raw := m.renderTaskList(m.taskRows)
	for _, bad := range []string{"\x1b[2J", "\x1b[H", "\x1b]52", "\x07", "‮", "​", "\r"} {
		if strings.Contains(raw, bad) {
			t.Errorf("card still carries %q", bad)
		}
	}
	if !strings.Contains(plain(m), "fix bug") {
		t.Errorf("step text lost its words:\n%s", plain(m))
	}
}

func TestUnknownStatusesReadAsPending(t *testing.T) {
	m := feed(t, newTestChat(t), listOf("running", "done", event.TaskInProgress))
	out := plain(m)
	if strings.Count(out, "[ ]") != 2 || strings.Count(out, "[>]") != 1 {
		t.Errorf("unknown statuses drawn as something they are not:\n%s", out)
	}
}

func TestADoneListSaysStillWorkingWhileTheTurnRuns(t *testing.T) {
	finished := listOf(event.TaskCompleted, event.TaskCompleted)
	finished.CurrentAction = "Writing file: notes.txt"
	m := feed(t, newTestChat(t), finished)
	if out := plain(m); !strings.Contains(out, "2 of 2 · still working") || strings.Contains(out, "done") {
		t.Errorf("a running turn claims to be done:\n%s", out)
	}
	// A list an earlier turn finished has no live action: it is simply done.
	m = feed(t, m, listOf(event.TaskCompleted, event.TaskCompleted))
	if out := plain(m); !strings.Contains(out, "2 of 2 done") {
		t.Errorf("an earlier turn's finished list reads as in progress:\n%s", out)
	}
	m.streaming = false
	m.resize()
	if out := plain(m); !strings.Contains(out, "2 of 2 done") {
		t.Errorf("a finished list does not say so:\n%s", out)
	}
}

func TestAStaleListSaysSoOnlyWhileTheAgentWorks(t *testing.T) {
	stale := listOf(event.TaskInProgress, event.TaskPending)
	stale.StepsSinceUpdate = staleAfterSteps + 3
	m := feed(t, newTestChat(t), stale)
	if out := plain(m); !strings.Contains(out, fmt.Sprintf("not updated for %d steps", staleAfterSteps+3)) {
		t.Errorf("a list the model stopped keeping looks current:\n%s", out)
	}
	fresh := stale
	fresh.StepsSinceUpdate = staleAfterSteps - 1
	if out := plain(feed(t, m, fresh)); strings.Contains(out, "not updated") {
		t.Errorf("a fresh list is flagged stale:\n%s", out)
	}
	m.streaming = false
	m.resize()
	if out := plain(m); strings.Contains(out, "not updated") {
		t.Errorf("an idle card is flagged stale:\n%s", out)
	}
}

func TestAnOversizedListIsCutAndNeverCalledDone(t *testing.T) {
	statuses := make([]string, 500)
	for i := range statuses {
		statuses[i] = event.TaskCompleted
	}
	statuses[499] = event.TaskPending
	big := listOf(statuses...)
	big.Items[0].Content = strings.Repeat("x", 100_000)
	m := feed(t, newTestChat(t), big)
	if len(m.taskList.Items) != maxTaskItems || m.taskHidden != 500-maxTaskItems {
		t.Fatalf("kept %d, hid %d", len(m.taskList.Items), m.taskHidden)
	}
	out := plain(m)
	if strings.Contains(out, "done") || strings.Contains(out, "still working") {
		t.Errorf("a list with hidden open steps reads as finished:\n%s", out)
	}
	if !strings.Contains(out, "of 500") {
		t.Errorf("the count ignores hidden steps:\n%s", out)
	}
	for _, l := range strings.Split(m.renderTaskList(m.taskRows), "\n") {
		if w := lipgloss.Width(l); w > m.width {
			t.Errorf("row is %d cells on a %d-column terminal", w, m.width)
		}
	}
	if h := lipgloss.Height(m.View()); h != m.height {
		t.Errorf("frame is %d rows on a %d-row terminal", h, m.height)
	}
}

// Every terminal size from tiny to large keeps the frame exactly the window.
func TestTheFrameFitsEveryWindowSize(t *testing.T) {
	for _, w := range []int{20, 40, 80, 200} {
		for _, h := range []int{8, 12, 24, 60} {
			m := newTestChat(t)
			m.width, m.height = w, h
			m.resize()
			m = feed(t, m, listOf(event.TaskCompleted, event.TaskInProgress, event.TaskPending, event.TaskPending))
			if got := lipgloss.Height(m.View()); got != h {
				t.Errorf("%dx%d: frame is %d rows", w, h, got)
			}
			for _, l := range strings.Split(m.renderTaskList(m.taskRows), "\n") {
				if lipgloss.Width(l) > w {
					t.Errorf("%dx%d: row overflows: %q", w, h, ansi.Strip(l))
				}
			}
		}
	}
}

func TestRapidUpdatesLeaveOnlyTheLatestList(t *testing.T) {
	m := newTestChat(t)
	for n := 0; n < 1000; n++ {
		e := listOf(event.TaskCompleted, event.TaskInProgress)
		e.Items[1].Content = fmt.Sprintf("update %d", n)
		m = feed(t, m, e)
	}
	if !strings.Contains(plain(m), "update 999") {
		t.Errorf("the card is not showing the newest list:\n%s", plain(m))
	}
}

func BenchmarkTaskListRender(b *testing.B) {
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 120, 40
	m.resize()
	m.streaming = true
	e := listOf(event.TaskCompleted, event.TaskCompleted, event.TaskInProgress, event.TaskPending, event.TaskPending,
		event.TaskPending, event.TaskPending, event.TaskPending, event.TaskPending, event.TaskPending, event.TaskPending, event.TaskPending)
	m.setTaskList(&e)
	m.updateViewport()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = m.renderTaskList(m.taskRows)
	}
}

func BenchmarkFrameWithTaskList(b *testing.B) {
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 120, 40
	m.resize()
	m.streaming = true
	e := listOf(event.TaskCompleted, event.TaskInProgress, event.TaskPending, event.TaskPending)
	m.setTaskList(&e)
	m.updateViewport()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = m.View()
	}
}

func BenchmarkFrameWithoutTaskList(b *testing.B) {
	m := NewChatModel(&nullClient{}, "gaia", "", false)
	m.width, m.height = 120, 40
	m.resize()
	m.streaming = true
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = m.View()
	}
}
