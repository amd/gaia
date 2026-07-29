package components

import (
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// RiskTier classifies a confirmation-gated action for the badge on the modal.
//
// Only Write and Destructive are ever reachable from a live
// event.CanonicalNeedsConfirmationEvent: a `read` tool never asks for
// confirmation in the first place (nothing to gate), and a tool the sidecar
// refuses outright is a governance BLOCK, which rides the canonical `error`
// event, not `needs_confirmation` (spec §6.2, `policy_alert` -> `error`). Read
// and Denied are still real, tested values — this is a complete, reusable
// classification, not one trimmed to fit only what the wire happens to send
// today.
type RiskTier int

const (
	RiskRead RiskTier = iota
	RiskWrite
	RiskDestructive
	RiskDenied
)

var (
	badgeReadStyle = lipgloss.NewStyle().Bold(true).
			Foreground(theme.OnFill).Background(theme.AccentFillBG).Padding(0, 1)
	badgeWriteStyle = lipgloss.NewStyle().Bold(true).
			Foreground(theme.OnFill).Background(theme.InfoFillBG).Padding(0, 1)
	badgeDestructiveStyle = lipgloss.NewStyle().Bold(true).
				Foreground(theme.OnFill).Background(theme.DangerFillBG).Padding(0, 1)
	badgeDeniedStyle = lipgloss.NewStyle().Bold(true).
				Foreground(theme.OnSurface).Background(theme.SurfaceBG).Padding(0, 1)
)

// Badge is the short label shown on the modal. Colour is decoration only —
// each tier's word is also distinct, so the badge is legible with colour
// stripped (the same accessibility rule components.QuestionModel documents).
func (t RiskTier) Badge() string {
	switch t {
	case RiskRead:
		return badgeReadStyle.Render("READ")
	case RiskWrite:
		return badgeWriteStyle.Render("WRITE")
	case RiskDestructive:
		return badgeDestructiveStyle.Render("DESTRUCTIVE")
	case RiskDenied:
		return badgeDeniedStyle.Render("BLOCKED")
	default:
		return badgeDeniedStyle.Render("UNKNOWN")
	}
}

// confirmationRiskTiers classifies the nine tools the email agent gates on
// confirmation (agent.CONFIRMATION_REQUIRED_TOOLS in
// hub/agents/email/python/gaia_agent_email/agent.py). send/schedule/forward and
// the calendar RSVP actions are Write — external or state-changing, but not
// destructive on their own terms. permanent_delete and quarantine are
// Destructive: one is irreversible by name, the other pulls a message out of
// the inbox on the agent's own judgment about a security threat, where a false
// positive hides real mail.
var confirmationRiskTiers = map[string]RiskTier{
	"send_now":                    RiskWrite,
	"send_draft":                  RiskWrite,
	"schedule_send":               RiskWrite,
	"forward_message":             RiskWrite,
	"accept_invite":               RiskWrite,
	"decline_invite":              RiskWrite,
	"create_event_from_email":     RiskWrite,
	"permanent_delete":            RiskDestructive,
	"quarantine_phishing_message": RiskDestructive,
}

// ClassifyActionRisk returns the risk tier for a confirmation-gated action
// name. An action this client has never heard of still made the sidecar ask
// for confirmation, so it defaults to the MORE cautious tier — failing open to
// Write would understate a risk this build simply does not recognize yet.
func ClassifyActionRisk(action string) RiskTier {
	if tier, ok := confirmationRiskTiers[action]; ok {
		return tier
	}
	return RiskDestructive
}

// ConfirmState is where a confirmation sits in its state machine:
// pending -> approved / denied / timed-out. Once resolved it stays resolved —
// see ConfirmationModel.Update.
type ConfirmState int

const (
	ConfirmationPending ConfirmState = iota
	ConfirmationApproved
	ConfirmationDenied
	ConfirmationTimedOut
)

// ConfirmationTimeout is how long the modal waits before auto-denying. Client-
// side and independent of the transport: the current email sidecar's
// stateless D1 stub ends the run with its own refusal within the same stream
// read the `needs_confirmation` event arrived on (no server-side wait at all),
// so this bound matters most for a future resume-model peer that genuinely
// parks the run — see docs/spec/agent-ui-query-sse-contract.md §5.
const ConfirmationTimeout = 30 * time.Second

// ConfirmationTimeoutMsg is the client-side auto-deny tick for one
// confirmation. RunID lets a stale timer — left over from a confirmation that
// already resolved, or was superseded by a newer one on the same run — be
// told apart from the one it was started for.
type ConfirmationTimeoutMsg struct{ RunID string }

// StartConfirmationTimeout schedules the auto-deny tick for one confirmation.
func StartConfirmationTimeout(runID string) tea.Cmd {
	return tea.Tick(ConfirmationTimeout, func(time.Time) tea.Msg {
		return ConfirmationTimeoutMsg{RunID: runID}
	})
}

// ConfirmationDecidedMsg is emitted once a confirmation resolves — by key or
// by the 30s timeout — carrying what the caller needs to record the outcome
// and, when a live channel exists, deliver it.
type ConfirmationDecidedMsg struct {
	RunID  string
	Action string
	// ConfirmURL is only ever non-empty under the resume model (spec §5),
	// which no shipped sidecar implements today — see the doc comment on
	// ConfirmationModel.
	ConfirmURL string
	Approved   bool
	// TimedOut records that the decision came from the 30s auto-deny rather
	// than an explicit 'n'/Esc, so the caller can show the distinct warning
	// the issue's acceptance criteria require.
	TimedOut bool
}

// ConfirmationModel renders a needs_confirmation pause: the gated action, its
// summary, a risk-tier badge, and a y/n/Esc prompt that auto-denies after
// ConfirmationTimeout.
//
// Approving here does not by itself deliver anything. The shipping email
// sidecar's `/query` contract is the stateless stop-and-hand-off model (spec
// §5, D1): `needs_confirmation` is immediately followed, in the same stream
// read, by a synthesized `final` refusal — there is no server-side pause and
// no confirm endpoint to resume it (`ConfirmURL` is always empty). Denying is
// therefore always real (nothing was ever going to be sent either way);
// approving only becomes real delivery once a peer implements the resume
// model documented in the spec — the caller checks ConfirmURL before treating
// Approved as anything more than the user's recorded intent. Inventing a fake
// delivery path here is exactly the mistake ui/oneshot.go's writeWithheld
// already documents avoiding for the one-shot surface.
type ConfirmationModel struct {
	runID      string
	action     string
	summary    string
	confirmURL string
	tier       RiskTier
	state      ConfirmState
	width      int
}

// NewConfirmationModel builds the modal for one needs_confirmation event.
func NewConfirmationModel(runID, action, summary, confirmURL string) ConfirmationModel {
	return ConfirmationModel{
		runID:      runID,
		action:     action,
		summary:    summary,
		confirmURL: confirmURL,
		tier:       ClassifyActionRisk(action),
		state:      ConfirmationPending,
		width:      76,
	}
}

func (m ConfirmationModel) RunID() string       { return m.runID }
func (m ConfirmationModel) Action() string      { return m.action }
func (m ConfirmationModel) State() ConfirmState { return m.state }
func (m ConfirmationModel) Tier() RiskTier      { return m.tier }
func (m ConfirmationModel) Pending() bool       { return m.state == ConfirmationPending }

// confirmationBorderCols is how many columns the rounded border itself adds
// (1 left + 1 right) on top of the style's Width — which lipgloss treats as
// the padded-content box, border excluded. Left uncorrected, a modal asked
// for width w renders at w+2, quietly breaking the "fits the terminal"
// contract at exactly the edge a narrow pane most needs it — see #2518, the
// ragged-wrap defect in a neighbouring card this modal must not repeat.
const confirmationBorderCols = 2

// SetWidth fits the panel to w: View() never renders a line wider than w.
func (m *ConfirmationModel) SetWidth(w int) {
	if w < 24 {
		w = 24
	}
	m.width = w
}

// Update handles one key while the confirmation is pending. Only y/Y (approve)
// and n/N/Esc (deny) resolve it; every other key is swallowed so nothing
// approves — or denies — by accident. Once resolved, further input is a no-op:
// a decision is made once.
func (m ConfirmationModel) Update(msg tea.Msg) (ConfirmationModel, tea.Cmd) {
	if m.state != ConfirmationPending {
		return m, nil
	}
	key, ok := msg.(tea.KeyMsg)
	if !ok {
		return m, nil
	}
	switch key.String() {
	case "y", "Y":
		return m.decide(ConfirmationApproved, false)
	case "n", "N", "esc":
		return m.decide(ConfirmationDenied, false)
	}
	return m, nil
}

// ResolveTimeout applies the 30s auto-deny, but only if this confirmation is
// still pending and msg was started for THIS run — a stale timer for an
// already-resolved or superseded confirmation must not fire a second decision.
func (m ConfirmationModel) ResolveTimeout(msg ConfirmationTimeoutMsg) (ConfirmationModel, tea.Cmd) {
	if m.state != ConfirmationPending || msg.RunID != m.runID {
		return m, nil
	}
	return m.decide(ConfirmationDenied, true)
}

func (m ConfirmationModel) decide(state ConfirmState, timedOut bool) (ConfirmationModel, tea.Cmd) {
	if timedOut {
		state = ConfirmationTimedOut
	}
	m.state = state
	decided := ConfirmationDecidedMsg{
		RunID:      m.runID,
		Action:     m.action,
		ConfirmURL: m.confirmURL,
		Approved:   state == ConfirmationApproved,
		TimedOut:   timedOut,
	}
	return m, func() tea.Msg { return decided }
}

var (
	confirmationPanelStyle = lipgloss.NewStyle().
				Border(lipgloss.RoundedBorder()).
				BorderForeground(theme.Danger).
				Padding(0, 1)

	confirmationTitleStyle = lipgloss.NewStyle().Bold(true).Foreground(theme.Danger)
	confirmationBodyStyle  = lipgloss.NewStyle().Foreground(theme.Text)
	confirmationWarnStyle  = lipgloss.NewStyle().Bold(true).Foreground(theme.Warning)
	confirmationHintStyle  = lipgloss.NewStyle().Foreground(theme.Dim).Italic(true)
	confirmationOkStyle    = lipgloss.NewStyle().Foreground(theme.Success)
	confirmationNoStyle    = lipgloss.NewStyle().Foreground(theme.Danger)
)

// View renders the panel. Wrapping happens here and only here, exactly like
// QuestionModel.View — handing the wrapped result to a width-constrained style
// as well would re-wrap already-wrapped lines and shear the border.
func (m ConfirmationModel) View() string {
	inner := m.width - 4
	if inner < 16 {
		inner = 16
	}

	var lines []string
	lines = append(lines, m.tier.Badge()+" "+confirmationTitleStyle.Render("Confirm: "+m.action))
	lines = append(lines, "")

	summary := m.summary
	if summary == "" {
		summary = "Run '" + m.action + "'?"
	}
	lines = append(lines, confirmationBodyStyle.Render(WrapText(summary, inner)))

	if m.tier == RiskDestructive {
		lines = append(lines, "")
		lines = append(lines, confirmationWarnStyle.Render(WrapText(
			"This is a destructive action and may not be reversible.", inner)))
	}

	lines = append(lines, "")
	lines = append(lines, m.resultOrHint(inner))

	return confirmationPanelStyle.Width(m.width - confirmationBorderCols).Render(strings.Join(lines, "\n"))
}

func (m ConfirmationModel) resultOrHint(inner int) string {
	switch m.state {
	case ConfirmationApproved:
		return confirmationOkStyle.Render("approved")
	case ConfirmationDenied:
		return confirmationNoStyle.Render("denied")
	case ConfirmationTimedOut:
		return confirmationWarnStyle.Render(WrapText("timed out after 30s with no response — denied", inner))
	default:
		return confirmationHintStyle.Render(WrapText(
			"y approve · n/esc deny · auto-denies in 30s if you do nothing", inner))
	}
}
