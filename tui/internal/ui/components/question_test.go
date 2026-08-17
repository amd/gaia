package components

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func opts() []QuestionOption {
	return []QuestionOption{
		{Value: "google", Label: "Gmail", Description: "A gmail.com or Google Workspace account."},
		{Value: "microsoft", Label: "Outlook", Description: "An outlook.com or Microsoft 365 account."},
	}
}

func key(s string) tea.KeyMsg {
	switch s {
	case "enter":
		return tea.KeyMsg{Type: tea.KeyEnter}
	case "down":
		return tea.KeyMsg{Type: tea.KeyDown}
	case "up":
		return tea.KeyMsg{Type: tea.KeyUp}
	case "tab":
		return tea.KeyMsg{Type: tea.KeyTab}
	}
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(s)}
}

func answerOf(t *testing.T, cmd tea.Cmd) QuestionAnsweredMsg {
	t.Helper()
	if cmd == nil {
		t.Fatal("expected an answer command, got nil")
	}
	msg, ok := cmd().(QuestionAnsweredMsg)
	if !ok {
		t.Fatalf("expected QuestionAnsweredMsg, got %T", cmd())
	}
	return msg
}

// The options AND their descriptions must both render — the description is what
// tells the user what choosing an option actually does.
func TestQuestionRendersOptionsAndDescriptions(t *testing.T) {
	q := NewQuestionModel("q1", "Which mailbox should I connect?", opts(), true, false)
	q.SetWidth(76)
	view := q.View()

	for _, want := range []string{
		"Which mailbox should I connect?",
		"Gmail",
		"gmail.com or Google Workspace",
		"Outlook",
		"Microsoft 365",
		"[1]",
		"[2]",
	} {
		if !strings.Contains(view, want) {
			t.Errorf("view is missing %q:\n%s", want, view)
		}
	}
}

// No colour-only signals: selection is legible with every escape stripped.
func TestQuestionSelectionIsAsciiMarked(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), false, false)
	q.SetWidth(76)
	if !strings.Contains(stripANSI(q.View()), "> (*) [1] Gmail") {
		t.Errorf("first option not ASCII-marked as selected:\n%s", stripANSI(q.View()))
	}
	q, _ = q.Update(key("down"))
	plain := stripANSI(q.View())
	if !strings.Contains(plain, "> (*) [2] Outlook") {
		t.Errorf("cursor did not move to option 2:\n%s", plain)
	}
	if !strings.Contains(plain, "  ( ) [1] Gmail") {
		t.Errorf("option 1 not marked unselected:\n%s", plain)
	}
}

func TestQuestionEnterReturnsSelectedValue(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), false, false)
	q, _ = q.Update(key("down"))
	_, cmd := q.Update(key("enter"))
	if got := answerOf(t, cmd); got.Value != "microsoft" || got.RequestID != "q1" {
		t.Errorf("answer = %+v, want microsoft/q1", got)
	}
}

func TestQuestionNumberShortcutAnswersDirectly(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), true, false)
	_, cmd := q.Update(key("2"))
	if got := answerOf(t, cmd); got.Value != "microsoft" {
		t.Errorf("answer = %+v, want microsoft", got)
	}
}

func TestQuestionFreeTextIsReturnedVerbatim(t *testing.T) {
	q := NewQuestionModel("q1", "Which mailbox?", opts(), true, false)
	q.SetWidth(76)
	// Tab past both options onto the free-text row.
	q, _ = q.Update(key("tab"))
	q, _ = q.Update(key("tab"))
	for _, r := range "fastmail" {
		q, _ = q.Update(key(string(r)))
	}
	_, cmd := q.Update(key("enter"))
	if got := answerOf(t, cmd); got.Value != "fastmail" {
		t.Errorf("answer = %q, want fastmail", got.Value)
	}
}

// On the free-text row a digit is part of the answer, not a shortcut.
func TestQuestionDigitsAreTextOnFreeTextRow(t *testing.T) {
	q := NewQuestionModel("q1", "Which mailbox?", opts(), true, false)
	q, _ = q.Update(key("tab"))
	q, _ = q.Update(key("tab"))
	var cmd tea.Cmd
	for i := 0; i < 2; i++ {
		q, cmd = q.Update(key("2"))
		if cmd != nil {
			if _, isAnswer := cmd().(QuestionAnsweredMsg); isAnswer {
				t.Fatal("a digit typed into free text must not submit an option")
			}
		}
	}
	_, cmd = q.Update(key("enter"))
	if got := answerOf(t, cmd); got.Value != "22" {
		t.Errorf("answer = %q, want 22", got.Value)
	}
}

// A question with no options is still answerable: free text is forced on rather
// than rendering a dead end.
func TestQuestionWithNoOptionsIsFreeText(t *testing.T) {
	q := NewQuestionModel("q1", "Paste the client ID", nil, false, false)
	q.SetWidth(76)
	for _, r := range "abc.apps" {
		q, _ = q.Update(key(string(r)))
	}
	_, cmd := q.Update(key("enter"))
	if got := answerOf(t, cmd); got.Value != "abc.apps" {
		t.Errorf("answer = %q", got.Value)
	}
}

// A secret is never echoed — not on screen, not into the transcript label.
func TestQuestionSensitiveAnswerIsMasked(t *testing.T) {
	q := NewQuestionModel("q1", "Paste the client secret", nil, true, true)
	q.SetWidth(76)
	for _, r := range "hunter2" {
		q, _ = q.Update(key(string(r)))
	}
	if strings.Contains(stripANSI(q.View()), "hunter2") {
		t.Error("a sensitive answer must not be echoed on screen")
	}
	if got := q.AnswerLabel("hunter2"); got != "(hidden)" {
		t.Errorf("transcript label = %q, want (hidden)", got)
	}
}

func TestQuestionEmptyAnswerIsNotSubmitted(t *testing.T) {
	q := NewQuestionModel("q1", "Paste the client ID", nil, true, false)
	if _, cmd := q.Update(key("enter")); cmd != nil {
		t.Fatal("an empty free-text answer must not be submitted")
	}
}

// 80x24 is the floor: the panel must not overflow the terminal width.
func TestQuestionFitsEightyColumns(t *testing.T) {
	long := []QuestionOption{
		{
			Value: "reconnect",
			Label: "Reconnect Gmail",
			Description: "Opens your browser so you can sign in again. Your mail is " +
				"untouched and nothing is sent anywhere except to Google.",
		},
		{Value: "no", Label: "Not now", Description: "Change nothing. I'll ask again next time."},
	}
	q := NewQuestionModel("q1",
		"Your Gmail sign-in (kalin@example.com) has stopped working — the saved "+
			"credentials were rejected, which usually means access was revoked or expired.",
		long, true, false)
	q.SetWidth(76)
	for _, line := range strings.Split(stripANSI(q.View()), "\n") {
		if w := len([]rune(line)); w > 80 {
			t.Errorf("line is %d cols wide (max 80): %q", w, line)
		}
	}
}

func TestQuestionAnswerLabelPrefersOptionLabel(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), true, false)
	if got := q.AnswerLabel("google"); got != "Gmail" {
		t.Errorf("label = %q, want Gmail", got)
	}
	if got := q.AnswerLabel("fastmail"); got != "fastmail" {
		t.Errorf("free-text label = %q, want fastmail", got)
	}
}

// --- mouse: RowAt / WithCursor -------------------------------------------------

// RowAt(0) is always the question title — never a selectable row, whatever
// the question text does.
func TestRowAtTitleRowIsNotSelectable(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), false, false)
	q.SetWidth(76)
	if got := q.RowAt(0); got != -1 {
		t.Errorf("RowAt(0) = %d, want -1 (the title)", got)
	}
}

// Every screen row the two options actually render on must resolve back to
// the right option index — including their wrapped description lines, which
// span more than one screen row each.
func TestRowAtMapsEveryRenderedRowToItsOption(t *testing.T) {
	q := NewQuestionModel("q1", "Which mailbox should I connect?", opts(), true, false)
	q.SetWidth(76)
	lines := strings.Split(q.View(), "\n")

	seen := map[int]bool{}
	for row := range lines {
		if opt := q.RowAt(row); opt >= 0 {
			seen[opt] = true
		}
	}
	if !seen[0] || !seen[1] {
		t.Errorf("expected both options reachable via RowAt, got hits for %v across %d rows", seen, len(lines))
	}
}

// The mapping must be EXACT, not merely reachable: the screen row that shows
// an option's "[N]" marker must resolve to option N-1. A constant off-by-one
// (e.g. subtracting a border row the borderless panel no longer has) keeps
// every option reachable through its wrapped description lines while sending
// a click on the label itself to the row above — this pins the label row.
func TestRowAtOptionLabelRowResolvesToThatOption(t *testing.T) {
	q := NewQuestionModel("q1", "Which mailbox should I connect?", opts(), true, false)
	q.SetWidth(76)
	lines := strings.Split(q.View(), "\n")

	for i := range opts() {
		marker := "[" + string(rune('1'+i)) + "]"
		found := false
		for row, line := range lines {
			if strings.Contains(stripANSI(line), marker) {
				found = true
				if got := q.RowAt(row); got != i {
					t.Errorf("RowAt(%d) — the %s label row — = %d, want %d", row, marker, got, i)
				}
			}
		}
		if !found {
			t.Fatalf("no rendered row carries the %s marker", marker)
		}
	}
}

// A row past the panel's own content is not a row at all.
func TestRowAtOutOfRangeIsNotSelectable(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), false, false)
	q.SetWidth(76)
	if got := q.RowAt(9999); got != -1 {
		t.Errorf("RowAt(9999) = %d, want -1", got)
	}
}

// WithCursor is the mouse's entry point to the same cursor Update drives —
// it must move the highlighted option and its focus exactly like ↑/↓ does.
func TestWithCursorMovesTheHighlightedOption(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), false, false)
	q.SetWidth(76)
	q = q.WithCursor(1)

	if q.Cursor() != 1 {
		t.Errorf("Cursor() = %d, want 1", q.Cursor())
	}
	if !strings.Contains(stripANSI(q.View()), "> (*) [2] Outlook") {
		t.Errorf("WithCursor(1) did not mark option 2 selected:\n%s", stripANSI(q.View()))
	}
}

// An out-of-range row is a no-op — a stale mouse coordinate must never move
// the cursor somewhere the keyboard path could not reach either.
func TestWithCursorIgnoresAnOutOfRangeRow(t *testing.T) {
	q := NewQuestionModel("q1", "Pick one", opts(), false, false)
	q.SetWidth(76)
	before := q.Cursor()

	q = q.WithCursor(-1)
	if q.Cursor() != before {
		t.Errorf("WithCursor(-1) moved the cursor to %d", q.Cursor())
	}
	q = q.WithCursor(99)
	if q.Cursor() != before {
		t.Errorf("WithCursor(99) moved the cursor to %d", q.Cursor())
	}
}

// stripANSI removes SGR escapes so assertions read the plain text a
// no-colour terminal would show.
func stripANSI(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); {
		if s[i] == 0x1b {
			for i < len(s) && s[i] != 'm' {
				i++
			}
			i++
			continue
		}
		b.WriteByte(s[i])
		i++
	}
	return b.String()
}

// The panel's right border must land in the same column on every line. A
// double-wrapped long question sheared it by one column (#2469 live capture).
func TestQuestionPanelBorderDoesNotShear(t *testing.T) {
	q := NewQuestionModel("q1",
		"Your Gmail sign-in has stopped working — the saved credentials were rejected, "+
			"which usually means the access was revoked or expired. I can take you "+
			"through signing in again.",
		[]QuestionOption{
			{Value: "yes", Label: "Reconnect Gmail", Description: "Opens your browser to sign in again. Your mail is untouched."},
			{Value: "no", Label: "Not now", Description: "Change nothing. I'll ask again next time."},
		}, true, false)
	q.SetWidth(96)

	lines := strings.Split(stripANSI(q.View()), "\n")
	want := len([]rune(lines[0]))
	for i, line := range lines {
		if got := len([]rune(line)); got != want {
			t.Errorf("line %d is %d cols wide, want %d: %q", i, got, want, line)
		}
	}
}
