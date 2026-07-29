package hub

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/catalog"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// trustDecisionMsg is the user's answer to the trust gate. Approved is true
// ONLY when they explicitly chose "Trust & Install".
type trustDecisionMsg struct {
	AgentID  string
	Version  string
	Approved bool
}

// trustModel is the install trust gate.
//
// It is deliberately not components.ConfirmModel: a yes/no box that says
// "Install this?" gives the user nothing to decide with. Installing a
// non-verified agent runs third-party code on their machine, so the prompt has
// to name what they are agreeing to — which agent, which version, whose
// package, which security tier, how much it downloads, and what it wants
// access to. The daemon's 403 is what forces this screen to exist; auto-
// retrying with trusted=true would defeat the entire gate.
type trustModel struct {
	agentID     string
	name        string
	version     string
	publisher   string
	security    string
	size        string
	permissions []string
	// reason is the daemon's own 403 detail, shown verbatim so a policy change
	// on the daemon side is never hidden behind this client's paraphrase.
	reason  string
	approve bool // true = "Trust & Install" focused; starts false
}

func newTrustModel(a catalog.Agent, reason string) trustModel {
	version := a.LatestVersion
	if version == "" {
		version = a.Version
	}
	return trustModel{
		agentID:     a.ID,
		name:        a.Name,
		version:     version,
		publisher:   a.Publisher(),
		security:    a.SecurityLabel(),
		size:        catalog.FormatSize(a.DownloadSizeBytes),
		permissions: a.Permissions,
		reason:      reason,
		approve:     false,
	}
}

func (m trustModel) Update(msg tea.Msg) (trustModel, tea.Cmd) {
	key, ok := msg.(tea.KeyMsg)
	if !ok {
		return m, nil
	}
	decide := func(approved bool) tea.Cmd {
		id, version := m.agentID, m.version
		return func() tea.Msg {
			return trustDecisionMsg{AgentID: id, Version: version, Approved: approved}
		}
	}
	switch key.String() {
	case "left", "right", "tab", "shift+tab":
		m.approve = !m.approve
	case "enter":
		return m, decide(m.approve)
	case "y", "Y":
		return m, decide(true)
	case "n", "N", "esc", "q":
		return m, decide(false)
	}
	return m, nil
}

var (
	trustBorder = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(theme.Warning).
			Padding(1, 2)

	trustTitleStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Warning)
	trustKeyStyle   = lipgloss.NewStyle().Foreground(theme.Dim)
	trustValStyle   = lipgloss.NewStyle().Foreground(theme.Text)
	trustWarnStyle  = lipgloss.NewStyle().Foreground(theme.Warning)
	trustHintStyle  = lipgloss.NewStyle().Foreground(theme.Dim)

	dangerBtnFocused = lipgloss.NewStyle().
				Bold(true).
				Foreground(theme.OnFill).
				Background(theme.WarnFillBG).
				Padding(0, 2)

	safeBtnFocused = lipgloss.NewStyle().
			Bold(true).
			Foreground(theme.OnFill).
			Background(theme.AccentFillBG).
			Padding(0, 2)

	btnDim = lipgloss.NewStyle().Foreground(theme.Dim).Padding(0, 2)
)

// fixedTrustRows is everything in the box except the daemon's reason: borders,
// padding, title, warning, the six-row fact table, the buttons, and the hint.
const fixedTrustRows = 18

func (m trustModel) View(width, height int) string {
	boxWidth := width - 8
	if boxWidth > 64 {
		boxWidth = 64
	}
	if boxWidth < 34 {
		boxWidth = 34
	}
	// The facts are what the user decides on and must never be pushed off
	// screen by a long daemon message, so the message is what gets clipped —
	// down to nothing on a terminal too short to hold both.
	reasonRows := height - fixedTrustRows
	if reasonRows > 6 {
		reasonRows = 6
	}
	if reasonRows < 0 {
		reasonRows = 0
	}

	row := func(k, v string) string {
		return trustKeyStyle.Render(fmt.Sprintf("  %-11s", k)) + trustValStyle.Render(v)
	}

	perms := "none declared"
	if len(m.permissions) > 0 {
		perms = strings.Join(m.permissions, ", ")
	}

	var b strings.Builder
	b.WriteString(trustTitleStyle.Render("Install " + m.name + "?"))
	b.WriteString("\n\n")
	b.WriteString(trustWarnStyle.Render("This downloads and runs code GAIA has not verified."))
	b.WriteString("\n\n")
	b.WriteString(row("Agent", m.agentID) + "\n")
	b.WriteString(row("Version", orUnknown(m.version)) + "\n")
	b.WriteString(row("Publisher", m.publisher) + "\n")
	b.WriteString(row("Security", m.security) + "\n")
	b.WriteString(row("Download", m.size) + "\n")
	b.WriteString(row("Access", perms) + "\n")
	if m.reason != "" && reasonRows > 0 {
		b.WriteString("\n" + trustHintStyle.Render(clipLines(wrap(m.reason, boxWidth-4), reasonRows)))
	}
	b.WriteString("\n\n")

	yes, no := btnDim.Render("Trust & Install"), safeBtnFocused.Render("Cancel")
	if m.approve {
		yes, no = dangerBtnFocused.Render("Trust & Install"), btnDim.Render("Cancel")
	}
	b.WriteString(yes + "  " + no + "\n")
	b.WriteString(trustHintStyle.Render("y trust & install · n/esc cancel · ←/→ move"))

	return trustBorder.Width(boxWidth).Render(b.String())
}

func (m trustModel) Overlay(background string, width, height int) string {
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, m.View(width, height))
}

// clipLines keeps at most max lines, marking that it truncated. Only ever
// applied to the daemon's supporting prose — never to the facts the user is
// deciding on.
func clipLines(s string, max int) string {
	lines := strings.Split(s, "\n")
	if len(lines) <= max {
		return s
	}
	return strings.Join(lines[:max], "\n") + " …"
}

func orUnknown(s string) string {
	if s == "" {
		return "unknown"
	}
	return s
}

// wrap breaks s onto lines of at most width columns, on spaces.
func wrap(s string, width int) string {
	if width < 12 {
		width = 12
	}
	words := strings.Fields(s)
	if len(words) == 0 {
		return ""
	}
	var lines []string
	line := words[0]
	for _, w := range words[1:] {
		if len(line)+1+len(w) > width {
			lines = append(lines, line)
			line = w
			continue
		}
		line += " " + w
	}
	return strings.Join(append(lines, line), "\n")
}
