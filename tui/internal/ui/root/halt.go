package root

import (
	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/ui/status"
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

// Halted reports whether the active screen is holding on a consequential
// Outcome. RootModel does not act on this itself — it neither renders
// anything nor intercepts a key for it. The screen that raised it
// (preflight.Model) already pauses itself and explains why; this is purely
// a signal ControlSnapshot's Overlay exposes to automation, so it can wait
// on "this TUI is waiting" without scraping the screen.
func (m FlagshipModel) Halted() bool { return len(m.halted) > 0 }

// applyOutcome runs o past every listener. A StepID already suppressed this
// session (the user has already proceeded past it once, see
// suppressHalted) never halts again; an already-tracked StepID is not added
// twice.
func (m FlagshipModel) applyOutcome(o status.Outcome) (tea.Model, tea.Cmd) {
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

// suppressHalted marks every currently-halted StepID as accepted for the
// rest of the session and clears halted. Called when the gate the Outcomes
// belonged to proceeds (see proceedFromGate) — proceeding past a
// consequential row IS the deliberate choice the Halt disposition exists to
// gate, so the same row halting again on a later launch this session would
// be the exact confirm-fatigue the Notify/Halt split exists to avoid. It
// does not change what the screen itself asks for on the next launch — only
// what automation is told about it.
func (m *FlagshipModel) suppressHalted() {
	if len(m.halted) == 0 {
		return
	}
	if m.suppressed == nil {
		m.suppressed = map[string]bool{}
	}
	for _, o := range m.halted {
		m.suppressed[o.StepID] = true
	}
	m.halted = nil
}
