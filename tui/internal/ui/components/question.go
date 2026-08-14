package components

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// QuestionModel renders a mid-run question from the agent: the question itself,
// its mutually-exclusive options with a description of what each one DOES, and
// an always-available free-text escape.
//
// It is deliberately more than a Yes/No box (ConfirmModel): "Gmail or Outlook?"
// and "use the default scopes, or pick them?" cannot be expressed as a binary,
// and a label alone ("Reconnect") does not tell the user what they are agreeing
// to. This is also the primitive an approval should eventually use — an
// approval is a question whose options are Approve and Deny — so nothing here
// is specific to onboarding.
//
// Rendered INLINE in the transcript rather than as a centred modal: at 80x24 a
// modal covers the conversation the question is about, and the answer belongs in
// the scrollback next to it.
//
// Accessibility: every state is carried by ASCII (`>` cursor, `(*)` / `( )`
// radio markers, `[n]` shortcuts). Colour is decoration only — the component is
// fully usable with it stripped.
type QuestionModel struct {
	requestID     string
	question      string
	options       []QuestionOption
	allowFreeText bool
	sensitive     bool

	// cursor indexes options; len(options) is the free-text row when allowed.
	cursor int
	input  textinput.Model
	width  int
}

// QuestionOption is one answer. Value goes back on the wire; Label is what the
// user picks; Description says what choosing it will do.
type QuestionOption struct {
	Value       string
	Label       string
	Description string
}

// QuestionAnsweredMsg is emitted when the user commits an answer.
type QuestionAnsweredMsg struct {
	RequestID string
	Value     string
}

var (
	questionPanelStyle = lipgloss.NewStyle().
				Padding(0, 1)

	questionTitleStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Warning)

	questionSelectedStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.AccentBright)

	questionOptionStyle = lipgloss.NewStyle().Foreground(theme.Text)

	questionDescStyle = lipgloss.NewStyle().Foreground(theme.Dim)

	questionHintStyle = lipgloss.NewStyle().Foreground(theme.Dim).Italic(true)
)

// NewQuestionModel builds the picker. A question with no options and no free
// text would be unanswerable, so free text is forced on in that case rather than
// rendering a dead end.
func NewQuestionModel(requestID, question string, options []QuestionOption, allowFreeText, sensitive bool) QuestionModel {
	if len(options) == 0 {
		allowFreeText = true
	}
	ti := textinput.New()
	// No prompt of its own: the row already carries the "> " cursor, and two
	// stacked chevrons read as a rendering bug.
	ti.Prompt = ""
	ti.Placeholder = "type your answer"
	ti.CharLimit = 2048
	if sensitive {
		ti.EchoMode = textinput.EchoPassword
	}

	m := QuestionModel{
		requestID:     requestID,
		question:      question,
		options:       options,
		allowFreeText: allowFreeText,
		sensitive:     sensitive,
		input:         ti,
		width:         76,
	}
	if len(options) == 0 {
		m.cursor = 0
		m.input.Focus()
	}
	return m
}

// RequestID identifies which question this is, so a stale answer is rejectable.
func (m QuestionModel) RequestID() string { return m.requestID }

// SetWidth fits the panel to the terminal.
func (m *QuestionModel) SetWidth(w int) {
	if w < 24 {
		w = 24
	}
	m.width = w
	m.input.Width = w - 8
}

func (m QuestionModel) onFreeText() bool {
	return m.allowFreeText && m.cursor == len(m.options)
}

func (m QuestionModel) rows() int {
	n := len(m.options)
	if m.allowFreeText {
		n++
	}
	return n
}

// Update handles one key. It returns the possibly-updated model and a Cmd that
// emits QuestionAnsweredMsg once an answer is committed.
func (m QuestionModel) Update(msg tea.Msg) (QuestionModel, tea.Cmd) {
	key, ok := msg.(tea.KeyMsg)
	if !ok {
		return m, nil
	}

	switch key.String() {
	case "up", "shift+tab":
		m.cursor = (m.cursor - 1 + m.rows()) % m.rows()
		m.syncFocus()
		return m, nil
	case "down", "tab":
		m.cursor = (m.cursor + 1) % m.rows()
		m.syncFocus()
		return m, nil
	case "enter":
		return m.commit()
	}

	// Number shortcuts pick an option directly — but only while the free-text
	// field does not have focus, or typing "2" into an answer would submit it.
	if !m.onFreeText() && len(key.Runes) == 1 {
		if n := int(key.Runes[0] - '1'); n >= 0 && n < len(m.options) {
			m.cursor = n
			return m.commit()
		}
	}

	if m.onFreeText() {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m *QuestionModel) syncFocus() {
	if m.onFreeText() {
		m.input.Focus()
		return
	}
	m.input.Blur()
}

// Cursor is the currently highlighted row — an option index, or
// len(options) for the free-text row.
func (m QuestionModel) Cursor() int { return m.cursor }

// WithCursor moves the highlighted row to n (an option index, or
// len(options) for free text). A n outside that range is a no-op, so a
// stale mouse coordinate (SetWidth changed the row count under it, say)
// cannot land the cursor somewhere Update's own key handling could never
// put it. This is the mouse's entry point to the same cursor the keyboard
// path (Update) drives — see handleQuestionMouse in the chat package.
func (m QuestionModel) WithCursor(n int) QuestionModel {
	if n < 0 || n >= m.rows() {
		return m
	}
	m.cursor = n
	m.syncFocus()
	return m
}

func (m QuestionModel) commit() (QuestionModel, tea.Cmd) {
	value := ""
	if m.onFreeText() {
		value = strings.TrimSpace(m.input.Value())
	} else if m.cursor >= 0 && m.cursor < len(m.options) {
		value = m.options[m.cursor].Value
	}
	if value == "" {
		// An empty answer is not an answer; leave the question up rather than
		// sending "" and having the agent reject it a round-trip later.
		return m, nil
	}
	id := m.requestID
	return m, func() tea.Msg {
		return QuestionAnsweredMsg{RequestID: id, Value: value}
	}
}

// AnswerLabel renders value the way it should appear in the transcript: the
// option's label when it matches one, the raw text otherwise — and never the
// text itself for a sensitive question.
func (m QuestionModel) AnswerLabel(value string) string {
	for _, opt := range m.options {
		if opt.Value == value {
			return opt.Label
		}
	}
	if m.sensitive {
		return "(hidden)"
	}
	return value
}

// questionLine is one rendered line of the panel's body (everything inside
// the border), paired with which selectable row it belongs to — an option
// index, len(options) for the free-text row, or -1 for chrome (the title, a
// wrapped description continuation, the hint) that a click should not select.
type questionLine struct {
	text string
	row  int
}

// layout builds the panel's body as View sees it, one questionLine per
// rendered row. The single source both View (which only needs .text) and
// RowAt (which only needs .row) draw from, so a click can never disagree with
// what is actually on screen — a hand-maintained second copy of this
// bookkeeping is exactly how that kind of drift happens.
func (m QuestionModel) layout(inner int) []questionLine {
	var lines []questionLine

	// The "? " marker occupies two columns on the first line, so the text
	// wraps two columns narrower and continuation lines hang under it.
	for _, l := range strings.Split(hang(WrapText(m.question, inner-2), "? ", "  "), "\n") {
		lines = append(lines, questionLine{questionTitleStyle.Render(l), -1})
	}

	for i, opt := range m.options {
		marker := "( )"
		cursor := "  "
		label := questionOptionStyle.Render(opt.Label)
		if i == m.cursor && !m.onFreeText() {
			marker = "(*)"
			cursor = "> "
			label = questionSelectedStyle.Render(opt.Label)
		}
		lines = append(lines, questionLine{
			fmt.Sprintf("%s%s [%d] %s", cursor, marker, i+1, label), i,
		})
		if opt.Description != "" {
			for _, l := range strings.Split(WrapText("      "+opt.Description, inner), "\n") {
				lines = append(lines, questionLine{questionDescStyle.Render(l), i})
			}
		}
	}

	if m.allowFreeText {
		cursor := "  "
		if m.onFreeText() {
			cursor = "> "
		}
		row := len(m.options)
		switch {
		case len(m.options) > 0:
			lines = append(lines, questionLine{cursor + questionDescStyle.Render("or type an answer:"), row})
			lines = append(lines, questionLine{"  " + m.input.View(), row})
		default:
			lines = append(lines, questionLine{cursor + m.input.View(), row})
		}
	}

	hint := "up/down or tab move · enter answer · esc cancel the turn"
	if len(m.options) > 0 {
		hint = "1-" + fmt.Sprint(len(m.options)) + " pick · " + hint
	}
	for _, l := range strings.Split(WrapText(hint, inner), "\n") {
		lines = append(lines, questionLine{questionHintStyle.Render(l), -1})
	}

	return lines
}

func (m QuestionModel) innerWidth() int {
	inner := m.width - 4
	if inner < 16 {
		inner = 16
	}
	return inner
}

// View renders the inline question panel.
//
// Wrapping is done in layout, and ONLY there. Handing an already-wrapped
// multi-line string to a lipgloss.Width() style AGAIN re-wraps it and shears
// the panel's right border by a column.
func (m QuestionModel) View() string {
	lines := m.layout(m.innerWidth())
	parts := make([]string, len(lines))
	for i, l := range lines {
		parts[i] = l.text
	}
	return questionPanelStyle.Width(m.width).Render(strings.Join(parts, "\n"))
}

// RowAt maps row — a screen row measured from the TOP OF View()'s rendered
// output (0 is the panel's own top border) — to the selectable row it
// belongs to: an option index, len(options) for the free-text row, or -1 for
// a border/chrome row a click should not act on.
//
// questionPanelStyle pads only horizontally (Padding(0, 1)), so exactly one
// row of chrome — the top border — sits above the body layout returns.
func (m QuestionModel) RowAt(row int) int {
	const topChrome = 1
	body := row - topChrome
	lines := m.layout(m.innerWidth())
	if body < 0 || body >= len(lines) {
		return -1
	}
	return lines[body].row
}

// hang prefixes the first line of s with first and every later line with rest,
// so a wrapped block reads as one hanging-indented paragraph.
func hang(s, first, rest string) string {
	lines := strings.Split(s, "\n")
	for i := range lines {
		if i == 0 {
			lines[i] = first + lines[i]
			continue
		}
		lines[i] = rest + lines[i]
	}
	return strings.Join(lines, "\n")
}

// WrapText hard-wraps text at limit columns on word boundaries, preserving each
// line's leading indent on its continuations.
//
// The viewport does NOT soft-wrap: a line longer than the pane is CLIPPED, and a
// clipped message loses its tail — which for an actionable message is exactly
// the part that says what to do. Anything rendered as a bare line rather than
// inside a width-constrained lipgloss block has to come through here.
func WrapText(s string, limit int) string {
	if limit <= 0 {
		return s
	}
	var out []string
	for _, para := range strings.Split(s, "\n") {
		indent := para[:len(para)-len(strings.TrimLeft(para, " "))]
		line := ""
		for _, word := range strings.Fields(para) {
			candidate := word
			if line != "" {
				candidate = line + " " + word
			}
			if lipgloss.Width(indent+candidate) > limit && line != "" {
				out = append(out, indent+line)
				line = word
				continue
			}
			line = candidate
		}
		out = append(out, indent+line)
	}
	return strings.Join(out, "\n")
}
