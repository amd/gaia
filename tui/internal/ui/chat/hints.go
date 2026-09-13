// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/x/ansi"
)

// The status bar is one row, and the hint shares it with the agent name. When
// the two do not fit, something has to go — and the interesting question is
// WHAT.
//
// Truncating the string is the wrong answer even though it is the easy one.
// The hint reads left to right in the order the items were concatenated, so a
// right-hand cut always eats the LAST item; "↑↓ scroll · Ctrl+C quit" at 20
// columns becomes "↑↓ scroll · Ctrl+C…", which drops the escape hatch and keeps
// the nice-to-have. On a narrow window that is exactly backwards: the smaller
// the screen, the more the user needs the way out and the less they need to be
// told the wheel scrolls.
//
// So hints carry a rank and whole items are dropped, lowest rank first, until
// the rest fit. Every item is either fully readable or absent — never a word
// ending in an ellipsis.
type hint struct {
	text string
	// rank orders survival, not display: lower rank is dropped first. Display
	// order is the order items were appended, which is what keeps the bar
	// stable as items come and go.
	rank int
}

// Hint ranks. The gaps are deliberate — they leave room to slot something in
// without renumbering.
const (
	// How to stop the agent acting on its own. Outranks even the way out:
	// while bypass is on, every frame the user cannot see this is a frame in
	// which tools are running unasked and they do not know how to stop it.
	rankBypass = 110
	// How to get out. Survives to the last column: a user who cannot see this
	// closes the terminal window.
	rankEscape = 100
	// How to stop what is happening now — nearly as urgent, and only shown
	// while there is something to stop.
	rankInterrupt = 90
	// Where you are, when you are somewhere unexpected. Only present while
	// scrolled away from the newest content, and then it is the way back.
	rankOrient = 70
	// What else the keyboard does. Genuinely useful, genuinely droppable.
	rankAffordance = 40
	// How to get the terminal's own drag-select back. Worth a column when
	// there is one to spare — the app holds the mouse by default, so a user
	// who tries to drag and gets nothing needs the way out — but it loses to
	// every hint that says what is happening right now.
	rankSecondary = 25
	// Numbers for whoever is tuning the machinery. First to go.
	rankDiagnostic = 10
)

// statusHints builds the hint list for the current state, in display order.
func (m ChatModel) statusHints() []hint {
	var hints []hint

	// The banner is the primary indicator; this is the belt to its braces, on
	// the one row that is always drawn.
	if m.bypassPermissions {
		hints = append(hints, hint{text: "/bypass off", rank: rankBypass})
	}

	if m.dev && m.totalSteps > 0 {
		// The agent loop's step count is a loop bound, not user progress — it
		// says neither what is happening nor how far along the work is. For
		// someone tuning the loop it is the number that matters, so it rides
		// the one row always on screen, and only in --dev.
		hints = append(hints, hint{text: stepHint(m.totalSteps), rank: rankDiagnostic})
	}
	if !m.followTail {
		hints = append(hints, hint{text: "End to jump to latest", rank: rankOrient})
	}

	// What the session has spent, on the one row that is always drawn. A cost
	// nobody sees until they think to ask for it is a cost nobody sees: the
	// point of putting it here is that it is in front of you while you decide
	// whether to ask the expensive follow-up.
	if spend := m.sessionSpendHint(); spend != "" {
		hints = append(hints, hint{text: spend, rank: rankSecondary})
	}

	// In an alt-screen app the wheel and the arrows are the ONLY way back to
	// earlier turns; a user who does not know that concludes history is gone.
	if m.mouseSelectMode {
		hints = append(hints, hint{text: "↑↓ scroll", rank: rankAffordance})
	} else {
		hints = append(hints, hint{text: "↑↓/wheel scroll", rank: rankAffordance})
		// Not while the agent is parked on a decision: the bar is a sentence,
		// and "answer above" is the only thing the reader should act on. How
		// to select text can wait for a frame where nothing is pending.
		if m.confirmation == nil {
			hints = append(hints, hint{text: "Ctrl+T select text", rank: rankSecondary})
		}
	}

	if m.confirmation != nil && m.confirmation.Pending() {
		// The modal owns the keyboard while it is up, so every hint here would
		// be a lie: typing goes nowhere and Esc denies rather than cancels.
		// Saying "Esc cancel" one row under a modal that says "esc deny" is
		// two answers to the same key on the same screen.
		//
		// The way out is NOT named here — rankEscape appends it below and
		// outranks everything, so spelling it again only got the bar saying
		// "Ctrl+C quits · Ctrl+C quit".
		hints = append(hints, hint{text: "answer above", rank: rankInterrupt})
	} else if m.streaming {
		// Worth advertising exactly when it applies: someone who believes the
		// composer is frozen never tries it.
		hints = append(hints,
			hint{text: "keep typing", rank: rankAffordance},
			hint{text: "Esc cancel", rank: rankInterrupt},
		)
	}

	hints = append(hints, hint{text: "Ctrl+C quit", rank: rankEscape})
	return hints
}

// fitHints joins what fits into width display columns, dropping whole items by
// ascending rank. Ties break toward the later item, so when two affordances
// compete the more contextual one — appended later — is the one kept.
//
// A width that cannot hold even the top-ranked item yields that item alone and
// lets the caller decide: the status bar clips it, which is the honest outcome
// for a terminal too narrow to say "Ctrl+C quit".
func fitHints(hints []hint, width int) string {
	if len(hints) == 0 {
		return ""
	}
	keep := make([]bool, len(hints))
	for i := range keep {
		keep[i] = true
	}

	for hintsWidth(hints, keep) > width {
		// Lowest rank still standing; on a tie the earliest, so the later
		// (more contextual) item outlives it.
		victim, found := -1, false
		for i, h := range hints {
			if !keep[i] {
				continue
			}
			if !found || h.rank < hints[victim].rank {
				victim, found = i, true
			}
		}
		if !found {
			break
		}
		keep[victim] = false

		// Everything has been dropped but one item that still does not fit.
		// Returning it whole beats returning nothing.
		if remaining(keep) == 1 {
			break
		}
	}

	var out []string
	for i, h := range hints {
		if keep[i] {
			out = append(out, h.text)
		}
	}
	return strings.Join(out, hintSeparator)
}

const hintSeparator = " · "

func hintsWidth(hints []hint, keep []bool) int {
	total, n := 0, 0
	for i, h := range hints {
		if !keep[i] {
			continue
		}
		total += ansi.StringWidth(h.text)
		n++
	}
	if n > 1 {
		total += (n - 1) * ansi.StringWidth(hintSeparator)
	}
	return total
}

func remaining(keep []bool) int {
	n := 0
	for _, k := range keep {
		if k {
			n++
		}
	}
	return n
}

func stepHint(steps int) string {
	return "step " + itoa(steps)
}

// itoa avoids pulling strconv in for one call site in a hot render path.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

// hintBudget is how many columns the hint may use on this terminal: whatever
// the status bar has left once the agent name and its dot and padding are
// accounted for.
func (m ChatModel) hintBudget() int {
	// " ● " + name, the bar's own padding, and a gap before the hint.
	used := 3 + ansi.StringWidth(m.agentName) + len(" connected") + 4
	return m.width - used
}

// sessionSpendHint is the running cost for the status bar, or "" when there is
// nothing worth saying.
//
// Only for a provider that bills per token. A local model costs nothing to run
// again, so a spend line there is noise on the one row that always has to
// carry the way out — and a gateway the organisation already pays for is not
// the user's spend to watch either.
//
// Dollars when a price is configured, tokens otherwise — never a guessed rate.
func (m ChatModel) sessionSpendHint() string {
	if len(m.cost.turns) == 0 || !m.isMeteredModel() {
		return ""
	}
	_, _, _, in, out, cached, _ := m.cost.totals()
	if in+out == 0 {
		return ""
	}
	if price := lookupPrice(m.modelID); price != nil {
		// Three decimals keep the status bar narrow, but a real spend must
		// never render as "$0.000" — that reads as free, and the /cost view
		// would disagree with it.
		usd := price.totalUSD(in, cached, out)
		if usd < 0.0005 {
			return "<$0.001 this session"
		}
		return fmt.Sprintf("$%.3f this session", usd)
	}
	return fmt.Sprintf("%s tok this session", thousands(in+out))
}

// isMeteredModel reports whether this session's inference is billed per token
// to the user.
//
// Keyed on the provider rather than on modelRemote alone: "remote" and "the
// user pays per token" are different claims, and an AMD gateway is remote
// without being the user's bill. Claude is the other way round — it is the
// most obviously metered thing the TUI runs, and it carries no provider
// prefix on its model id, so it has to be recognised by its backend.
func (m ChatModel) isMeteredModel() bool {
	if !m.modelRemote {
		return false
	}
	return m.modelBackend == "claude" || strings.HasPrefix(m.modelID, "fireworks.")
}
