package root

import (
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/status"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Listener decides whether an Outcome should hold the screen for a person.
// This is the subscribe seam the issue asks for: RootModel defaults to one
// entry (haltOnDisposition) and there is no registration API beyond it.
type Listener func(status.Outcome) bool

// haltOnDisposition is the default Listener. Notify is the only Disposition
// that skips holding — Halt, and a Disposition nobody decided, both hold.
// Same inverted direction as preflight.Report.HasHalt, and for the same
// reason: written the other way round, a row nobody assigned a Disposition
// to would silently proceed instead of loudly halting.
func haltOnDisposition(o status.Outcome) bool {
	return o.Level != status.LevelOK && o.Disposition != status.DispositionNotify
}

// Halted reports whether a halting Outcome currently owns the screen.
// Mirrors preflight.Model.Ready()/Busy() — an exported query over otherwise
// unexported state.
func (m RootModel) Halted() bool { return len(m.halted) > 0 }

// applyOutcome runs o past every listener. A dismissed StepID never halts
// again this session; an already-tracked StepID is not added twice.
func (m RootModel) applyOutcome(o status.Outcome) (tea.Model, tea.Cmd) {
	if m.suppressed[o.StepID] {
		return m, nil
	}
	halt := false
	for _, l := range m.listeners {
		if l(o) {
			halt = true
			break
		}
	}
	if !halt {
		return m, nil
	}
	for _, existing := range m.halted {
		if existing.StepID == o.StepID {
			return m, nil
		}
	}
	m.halted = append(m.halted, o)
	return m, nil
}

// handleHaltKey owns every key while the notice is up, extending the
// hand-ordered overlay chain showHelp and connectHandoff already use.
// ctrl+c quits like everywhere else — the halt is raised at the moment
// something is already wrong, the worst time to make the escape hatch need
// two presses. enter dismisses and suppresses every currently-halted StepID
// for the rest of the session, and does not reach the screen behind it.
// Every other key is swallowed.
func (m RootModel) handleHaltKey(key tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch key.String() {
	case "ctrl+c":
		return m, tea.Quit
	case "enter":
		suppressed := make(map[string]bool, len(m.suppressed)+len(m.halted))
		for k := range m.suppressed {
			suppressed[k] = true
		}
		for _, o := range m.halted {
			suppressed[o.StepID] = true
		}
		m.suppressed = suppressed
		m.halted = nil
		return m, nil
	}
	return m, nil
}

// orderedHaltRows returns a COPY of outcomes with a known failure ordered
// before an unproven row — the same precedence
// preflight.Model.unverifiedSummary already encodes, reused rather than
// reinvented.
func orderedHaltRows(outcomes []status.Outcome) []status.Outcome {
	out := append([]status.Outcome{}, outcomes...)
	sort.SliceStable(out, func(i, j int) bool {
		return haltPrecedence(out[i].Level) < haltPrecedence(out[j].Level)
	})
	return out
}

func haltPrecedence(l status.Level) int {
	switch l {
	case status.LevelFailed:
		return 0
	case status.LevelUnknown:
		return 1
	default:
		return 2
	}
}

var (
	haltBoxStyle   = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(theme.Warning).Padding(1, 2)
	haltTitleStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Warning)
	haltLabelStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Text)
	haltDimStyle   = lipgloss.NewStyle().Foreground(theme.Dim)
	haltKeyStyle   = lipgloss.NewStyle().Bold(true).Foreground(theme.Info)
)

// renderHaltNotice centers the halt notice over background, the same
// pattern components.RenderHelpOverlay uses for the help panel.
func renderHaltNotice(outcomes []status.Outcome, background string, width, height int) string {
	ordered := orderedHaltRows(outcomes)

	var b strings.Builder
	b.WriteString(haltTitleStyle.Render("Waiting for you"))
	b.WriteString("\n\n")
	for _, o := range ordered {
		b.WriteString(haltLabelStyle.Render(o.Label))
		if o.Summary != "" {
			b.WriteString(": " + haltDimStyle.Render(o.Summary))
		}
		b.WriteString("\n")
	}
	b.WriteString("\n")
	b.WriteString(haltKeyStyle.Render("enter") + " " + haltDimStyle.Render("continue anyway") +
		haltDimStyle.Render("  ·  ") + haltKeyStyle.Render("ctrl+c") + " " + haltDimStyle.Render("quit"))

	boxWidth := width - 4
	if boxWidth > 70 {
		boxWidth = 70
	}
	if boxWidth < 20 {
		boxWidth = 20
	}
	box := haltBoxStyle.Width(boxWidth).Render(strings.TrimRight(b.String(), "\n"))
	return lipgloss.Place(width, height, lipgloss.Center, lipgloss.Center, box)
}
