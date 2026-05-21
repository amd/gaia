package components

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type ConfirmResult int

const (
	ConfirmPending ConfirmResult = iota
	ConfirmYes
	ConfirmNo
)

type ConfirmMsg struct {
	ID     string
	Result ConfirmResult
}

type ConfirmModel struct {
	id       string
	title    string
	message  string
	yesLabel string
	noLabel  string
	focused  bool // true = Yes is focused
	width    int
}

func NewConfirmModel(id, title, message string) ConfirmModel {
	return ConfirmModel{
		id:       id,
		title:    title,
		message:  message,
		yesLabel: "Yes",
		noLabel:  "No",
		focused:  false, // default to No (safer)
		width:    50,
	}
}

var (
	confirmBorder = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("114")).
			Padding(1, 2)

	confirmTitle = lipgloss.NewStyle().Bold(true)

	btnFocused = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("230")).
			Background(lipgloss.Color("114")).
			Padding(0, 2)

	btnUnfocused = lipgloss.NewStyle().
			Foreground(lipgloss.Color("245")).
			Padding(0, 2)
)

func (m ConfirmModel) Init() tea.Cmd { return nil }

func (m ConfirmModel) Update(msg tea.Msg) (ConfirmModel, tea.Cmd) {
	if msg, ok := msg.(tea.KeyMsg); ok {
		switch msg.String() {
		case "left", "right", "tab", "shift+tab":
			m.focused = !m.focused
		case "enter":
			result := ConfirmNo
			if m.focused {
				result = ConfirmYes
			}
			return m, func() tea.Msg {
				return ConfirmMsg{ID: m.id, Result: result}
			}
		case "y", "Y":
			return m, func() tea.Msg {
				return ConfirmMsg{ID: m.id, Result: ConfirmYes}
			}
		case "n", "N", "esc":
			return m, func() tea.Msg {
				return ConfirmMsg{ID: m.id, Result: ConfirmNo}
			}
		}
	}
	return m, nil
}

func (m ConfirmModel) View() string {
	title := confirmTitle.Render(m.title)
	msg := m.message

	var yesBtn, noBtn string
	if m.focused {
		yesBtn = btnFocused.Render(m.yesLabel)
		noBtn = btnUnfocused.Render(m.noLabel)
	} else {
		yesBtn = btnUnfocused.Render(m.yesLabel)
		noBtn = btnFocused.Render(m.noLabel)
	}

	buttons := yesBtn + "  " + noBtn

	content := title + "\n\n" + msg + "\n\n" + buttons
	return confirmBorder.Width(m.width).Render(content)
}

func (m ConfirmModel) Overlay(background string, width, height int) string {
	dialog := m.View()
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, dialog)
}
