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

// confirmationRiskTiers classifies every gated tool this client knows about.
//
// The first group is the nine the email agent gates
// (agent.CONFIRMATION_REQUIRED_TOOLS in
// hub/agents/email/python/gaia_agent_email/agent.py). send/schedule/forward and
// the calendar RSVP actions are Write — external or state-changing, but not
// destructive on their own terms. permanent_delete and quarantine are
// Destructive: one is irreversible by name, the other pulls a message out of
// the inbox on the agent's own judgment about a security threat, where a false
// positive hides real mail.
//
// The second group is the base set every agent gates
// (agent.TOOLS_REQUIRING_CONFIRMATION in src/gaia/agents/base/agent.py) plus
// the flagship's own two. Left unlisted they all fell through to the cautious
// default and rendered "DESTRUCTIVE" — including a `pwd`. A badge that says
// DESTRUCTIVE for everything says nothing, and crying wolf on the safe calls
// is what makes the loud one ignorable.
//
// The shell tools stay Destructive because their name genuinely does not bound
// what they do: `run_shell_command` is `pwd` on one call and `rm -rf` on the
// next. The file writers are Write — scoped, and visible afterwards.
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

	"run_shell_command":   RiskDestructive,
	"run_cli_command":     RiskDestructive,
	"write_file":          RiskWrite,
	"write_python_file":   RiskWrite,
	"edit_file":           RiskWrite,
	"edit_python_file":    RiskWrite,
	"write_markdown_file": RiskWrite,
	"replace_function":    RiskWrite,
	"update_gaia_md":      RiskWrite,
	"install_skill":       RiskWrite,
	"remove_skill":        RiskWrite,
}

// unboundedRiskActions are tiered Destructive because their name does not bound
// what they do — NOT because the call in front of the user is destructive.
var unboundedRiskActions = map[string]bool{
	"run_shell_command": true,
	"run_cli_command":   true,
}

// destructiveWarning is the sentence shown beneath a RiskDestructive summary.
//
// Two different claims were hiding under one tier. permanent_delete IS
// destructive, by name. run_shell_command is not: it is *unbounded* — `pwd` on
// one call and `rm -rf` on the next — which is exactly why it is tiered
// cautiously (see confirmationRiskTiers). Telling someone that `pwd` "is a
// destructive action and may not be reversible" is false, and a warning a user
// has learned is false on the safe calls is the warning they will dismiss
// without reading on the dangerous one.
//
// So the unbounded case says the true thing instead, and points at the one
// piece of information that actually settles it: the command, already on screen
// directly above this line.
func destructiveWarning(action string) string {
	if unboundedRiskActions[action] {
		return "A shell command can read, change, or delete anything you can — " +
			"check the command above before approving."
	}
	return "This is a destructive action and may not be reversible."
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
// pending -> approved / always / denied / timed-out. Once resolved it stays
// resolved — see ConfirmationModel.Update.
type ConfirmState int

const (
	ConfirmationPending ConfirmState = iota
	ConfirmationApproved
	ConfirmationDenied
	ConfirmationTimedOut
	// ConfirmationAlways is approve-and-stop-asking. Kept distinct from
	// ConfirmationApproved because the two grant different things and the
	// transcript has to be able to say which one the user chose.
	ConfirmationAlways
)

// ConfirmationTimeout is how long an UNDELIVERABLE confirmation waits before
// auto-denying.
//
// It applies only where the answer has nowhere to go: the email sidecar's
// stateless D1 stub ends the run with its own refusal within the same stream
// read the `needs_confirmation` event arrived on, so the modal there is a
// record of intent, not a live question, and letting it expire costs nothing.
//
// Where the answer CAN be delivered the modal does not expire at all — see
// ConfirmationModel.Deliverable. A person reading "run `rm -rf build`?" and
// deciding routinely takes longer than 30s, and a prompt that silently denies
// under them loses work they were in the middle of approving. That was the
// real defect: the flagship agent's own turns died this way. Deny stays the
// default wherever a timeout does still apply — expiry never approves.
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
// by the auto-deny timeout — carrying what the caller needs to record the
// outcome and, when a live channel exists, deliver it.
type ConfirmationDecidedMsg struct {
	RunID  string
	Action string
	// ConfirmID identifies the prompt this decision was typed against, so a
	// late answer cannot resolve the confirmation that replaced it. Empty on
	// transports that do not mint one.
	ConfirmID string
	// ConfirmURL is only ever non-empty under the resume model (spec §5),
	// which no shipped sidecar implements today — see the doc comment on
	// ConfirmationModel.
	ConfirmURL string
	Approved   bool
	// Always is an approval that also stops the asking, for the scope named in
	// AlwaysScope — never for the whole tool. It implies Approved.
	Always bool
	// AlwaysScope is what that grant covers, exactly as the agent described it.
	AlwaysScope string
	// TimedOut records that the decision came from the auto-deny rather than
	// an explicit 'n'/Esc, so the caller can show the distinct warning the
	// issue's acceptance criteria require.
	TimedOut bool
	// Deliverable is whether the transport this decision came from can
	// actually carry it back to the agent. False means the modal recorded
	// intent only.
	Deliverable bool
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
	runID     string
	action    string
	summary   string
	confirmID string
	// confirmURL is the resume-model seam (spec §5); deliverable is the
	// live-channel one (the stdio control channel). Either makes the decision
	// real; neither makes the modal a record of intent.
	confirmURL  string
	deliverable bool
	// alwaysScope is what an "always" answer grants, as the AGENT described it
	// (e.g. `gh issue list`). Empty means no grant is on offer for this call.
	alwaysScope string
	tier        RiskTier
	state       ConfirmState
	width       int
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

// WithLiveChannel marks the decision as one the transport can actually deliver,
// and records the prompt id to echo back with it.
//
// This is what turns the modal from a record of the user's intent into a real
// question: a deliverable confirmation does not auto-deny, and it offers
// "always" — a grant that means nothing if nobody is listening for it.
func (m ConfirmationModel) WithLiveChannel(confirmID, alwaysScope string) ConfirmationModel {
	m.deliverable = true
	m.confirmID = confirmID
	m.alwaysScope = alwaysScope
	return m
}

func (m ConfirmationModel) RunID() string       { return m.runID }
func (m ConfirmationModel) Action() string      { return m.action }
func (m ConfirmationModel) State() ConfirmState { return m.state }
func (m ConfirmationModel) Tier() RiskTier      { return m.tier }
func (m ConfirmationModel) Pending() bool       { return m.state == ConfirmationPending }

// Deliverable reports whether answering this prompt actually reaches the agent.
func (m ConfirmationModel) Deliverable() bool { return m.deliverable }

// ExpiresUnanswered reports whether this confirmation auto-denies. Only an
// undeliverable one does; a live question waits for its human.
func (m ConfirmationModel) ExpiresUnanswered() bool { return !m.deliverable }

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

// Update handles one key while the confirmation is pending. Only y/Y (approve
// once), a/A (approve and stop asking) and n/N/Esc (deny) resolve it; every
// other key is swallowed so nothing approves — or denies — by accident. Once
// resolved, further input is a no-op: a decision is made once.
//
// "Always" is offered only on a deliverable prompt. Where the answer cannot
// reach the agent there is no session to grant anything for, so the key would
// promise a suppression that never happens.
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
	case "a", "A":
		if !m.allowAlways() {
			return m, nil
		}
		return m.decide(ConfirmationAlways, false)
	case "n", "N", "esc":
		return m.decide(ConfirmationDenied, false)
	}
	return m, nil
}

// allowAlways reports whether this prompt may be answered with "always".
//
// Two conditions, and the second is the important one. The decision has to be
// deliverable, AND the agent has to have said what a grant would cover. The
// client never invents that scope: an "always" the renderer decided the shape
// of would be a promise nothing enforces. No scope means no key — the user
// answers y/n each time, which is the correct outcome for a call too open-
// ended to describe (a bare `bash -c …`, say).
func (m ConfirmationModel) allowAlways() bool {
	return m.deliverable && m.alwaysScope != ""
}

// ResolveTimeout applies the auto-deny, but only if this confirmation can
// expire at all, is still pending, and msg was started for THIS run — a stale
// timer for an already-resolved or superseded confirmation must not fire a
// second decision.
func (m ConfirmationModel) ResolveTimeout(msg ConfirmationTimeoutMsg) (ConfirmationModel, tea.Cmd) {
	if !m.ExpiresUnanswered() || m.state != ConfirmationPending || msg.RunID != m.runID {
		return m, nil
	}
	return m.decide(ConfirmationDenied, true)
}

func (m ConfirmationModel) decide(state ConfirmState, timedOut bool) (ConfirmationModel, tea.Cmd) {
	if timedOut {
		state = ConfirmationTimedOut
	}
	m.state = state
	always := state == ConfirmationAlways
	decided := ConfirmationDecidedMsg{
		RunID:       m.runID,
		Action:      m.action,
		ConfirmID:   m.confirmID,
		ConfirmURL:  m.confirmURL,
		Approved:    state == ConfirmationApproved || always,
		Always:      always,
		AlwaysScope: m.alwaysScope,
		TimedOut:    timedOut,
		Deliverable: m.deliverable,
	}
	return m, func() tea.Msg { return decided }
}

var (
	confirmationPanelStyle = lipgloss.NewStyle().
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
			destructiveWarning(m.action), inner)))
	}

	lines = append(lines, "")
	lines = append(lines, m.resultOrHint(inner))

	return confirmationPanelStyle.Width(m.width - confirmationBorderCols).Render(strings.Join(lines, "\n"))
}

func (m ConfirmationModel) resultOrHint(inner int) string {
	switch m.state {
	case ConfirmationApproved:
		return confirmationOkStyle.Render("approved")
	case ConfirmationAlways:
		return confirmationOkStyle.Render(WrapText(
			"approved — and `"+m.alwaysScope+"` will not ask again this session", inner))
	case ConfirmationDenied:
		return confirmationNoStyle.Render("denied")
	case ConfirmationTimedOut:
		return confirmationWarnStyle.Render(WrapText("timed out after 30s with no response — denied", inner))
	default:
		return confirmationHintStyle.Render(WrapText(m.keyHint(), inner))
	}
}

// keyHint spells out each choice, and says what "always" actually grants.
//
// "always allow" alone reads as "allow this again", which says nothing about
// how much else it covers. The backend records an INVOCATION-scoped key, not
// the tool name (gaia/agents/base/tool_grants.py), so the honest label is the
// scope it sent us — `gh issue comment`, not `run_shell_command`. Printing that
// scope verbatim is the difference between informed consent and a pleasant
// surprise, and it is why an empty AlwaysScope hides the choice entirely.
func (m ConfirmationModel) keyHint() string {
	base := "y run once · n/esc deny"
	if m.allowAlways() {
		base = "y run once · a allow `" + m.alwaysScope +
			"` this session · n/esc deny"
	}
	if m.ExpiresUnanswered() {
		return base + " · auto-denies in 30s if you do nothing"
	}
	return base + " · waits for you"
}
