package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/ui/components"

	"github.com/amd/gaia/tui/internal/ui/theme"
)

// eventMsg and doneMsg carry the channel they came from. Bubble Tea cannot
// cancel an already-dispatched Cmd, so a cancelled turn's waitForEvent goroutine
// stays parked on its old channel and delivers late — without the tag, that late
// delivery would tear down whatever turn is running by then.
type eventMsg struct {
	ch    <-chan interface{}
	event interface{}
}

// errMsg fires from sendQuery's own Cmd, exactly when c.Send() failed to
// mint a channel in the first place — so unlike eventMsg/doneMsg it has no
// channel to key supersededTurn off of. turnSeq is its own scoping token
// instead: it captures ChatModel.turnSeq at the moment the Cmd was created,
// so a late delivery from an abandoned turn (the user already resent) can be
// told apart from the live turn's own failure. See ChatModel.turnSeq.
//
// turnSeq's zero value means "pre-first-turn" — sendQuery increments
// ChatModel.turnSeq before capturing it, so a real turn's errMsg always
// carries >= 1. Always set turnSeq from a live turn when constructing one;
// the Update guard also rejects 0 outright rather than only comparing
// against ChatModel.turnSeq, so an errMsg built without the field cannot
// accidentally pass by matching a fresh model's own zero-valued turnSeq.
type errMsg struct {
	err     error
	turnSeq int
}
type doneMsg struct{ ch <-chan interface{} }
type sendQueryMsg struct{ query string }
type channelReadyMsg struct{ ch <-chan interface{} }

// cancelRequestFailedMsg carries a failure of the out-of-band Cancel() call
// (client.AgentCanceler, #2901) — asking the server to stop the run, not the
// run itself. The run may still be live and settle on its own; this only
// reports that the ASK to stop it could not be delivered, so it never touches
// m.streaming/m.cancelPending — only the eventual doneMsg (or errMsg) for the
// run's own channel does that.
type cancelRequestFailedMsg struct{ err error }

// preScanFetchedMsg / preScanFetchFailedMsg / preScanDegradedMsg deliver the
// result of the on-open inbox pre-scan fetch (#2743, replacing the #2582
// attention fetch) — a side-channel read, never a chat turn, so it carries
// no query/answer pair and never touches the host-owned transcript Send()
// pushes as `context`.
type preScanFetchedMsg struct{ data json.RawMessage }
type preScanFetchFailedMsg struct{ err error }

// preScanDegradedMsg is delivered when the peer's contract predates
// needs_you (client.ErrPreScanContractTooOld) — rendered as an honest
// status note, never as the confident (and wrong) empty-needs_you card a
// naive decode of an old sidecar's response would produce.
type preScanDegradedMsg struct{ notice string }

// ReturnToHubMsg signals the root model to switch back to the hub view.
type ReturnToHubMsg struct{ AgentID string }

// ToggleHelpMsg signals the root model to toggle help overlay.
type ToggleHelpMsg struct{}

var (
	headerStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(theme.AccentBright).
			Padding(0, 1)

	// The user already knows what they typed. Their turn is a quiet landmark for
	// finding your place in the scrollback — never competition for the answer.
	userStyle = lipgloss.NewStyle().
			Foreground(theme.Dim)

	activityStyle = lipgloss.NewStyle().
			Foreground(theme.Dim)

	// Developer payload lines sit one rung below the outcome line they hang
	// under: in --dev they are the most numerous thing on screen, so they are
	// also the dimmest, keeping the narration readable through them.
	devPayloadStyle = lipgloss.NewStyle().
			Foreground(theme.Dim).
			Faint(true)

	toolNameStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(theme.Info)

	failStyle = lipgloss.NewStyle().
			Foreground(theme.Danger)

	dividerStyle = lipgloss.NewStyle().
			Foreground(theme.Divider)

	thinkingStyle = lipgloss.NewStyle().
			Foreground(theme.Success)

	statusMsgStyle = lipgloss.NewStyle().
			Foreground(theme.Dim).
			Italic(true)

	// No border. A green box round every answer drew the eye to the frame
	// instead of the words, and cost four columns and two rows per turn. The
	// answer is the brightest text on screen — that is what marks it.
	answerPanelStyle = lipgloss.NewStyle().
				Foreground(theme.Text).
				PaddingLeft(2)

	errorPanelStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(theme.Danger).
			Padding(0, 1)
)

type ChatModel struct {
	messages  []Message
	activity  []ActivityItem
	streaming bool
	// cancelPending is true from the moment Esc/Ctrl+C requests a cancel until
	// doneMsg confirms the run's channel actually closed. It exists only to
	// let the doneMsg handler distinguish "this settlement was a cancel" (so
	// it can append the confirmed "cancelled" line) from any other doneMsg
	// delivery — it plays no part in gating Enter, which is m.streaming's job.
	cancelPending bool
	// turnSeq increments on every sendQuery call. It scopes errMsg (see its
	// doc comment) the way eventMsg/doneMsg are scoped by m.events — a
	// channel identity doesn't exist yet when errMsg fires, so this integer
	// generation stands in for it instead.
	turnSeq int
	// buffer accumulates streamed answer text. A plain string, not a
	// strings.Builder: Bubble Tea copies the model on every update, and a
	// Builder panics the moment a copied non-zero one is written to again.
	buffer string

	// queued holds one follow-up typed while the agent was still working, sent
	// the moment the turn settles (see Update's drain). A local model routinely
	// takes 60-120s per turn; freezing the composer for that long forced the
	// user to hold their next thought in their head, or cancel to type it.
	// One slot, not a queue: a second Enter replaces it, which is what a person
	// who retypes actually means.
	queued string

	input    textarea.Model
	viewport viewport.Model
	spinner  spinner.Model

	client    client.AgentClient
	events    <-chan interface{}
	cancelFn  context.CancelFunc
	agentName string
	agentID   string
	dev       bool
	fromHub   bool

	width  int
	height int

	// question is the mid-run question the agent is parked on, if any. Non-nil
	// means the run is alive and waiting on THIS client — keystrokes go to it,
	// not to the composer.
	question *components.QuestionModel

	// confirmation is the pending needs_confirmation modal, if any. Non-nil
	// means a destructive/external tool call is asking for approval — every
	// key except Ctrl+C goes to it (including Esc, which means "deny" here,
	// not "cancel the turn" — see handleKey).
	confirmation *components.ConfirmationModel

	// bypassPermissions is true while the agent runs every gated tool without
	// asking. OFF on a fresh launch, always, and never restored from anywhere:
	// the zero value is the safe value, so there is no code path that can turn
	// it on without someone having asked for it in this session (or passed
	// --bypass-permissions on this launch).
	//
	// While it is true the UI owes the user an unmissable, unscrollable
	// statement of that fact — see renderBypassBanner.
	bypassPermissions bool
	// bypassArmed is set by /bypass and cleared by the next key. Turning
	// autonomy ON is a two-step confirmation; turning it OFF is one key, and
	// never gated.
	bypassArmed bool

	// claudeMode is true while the agent's inference runs on Anthropic's
	// Claude API instead of the local Lemonade backend (--use-claude). Set
	// once at launch from the transport's argv — see applyLaunchClaude — and
	// kept in sync by every model-state ping thereafter (handleCanonicalEvent).
	claudeMode bool

	// modelID/modelDisplay/modelBackend/modelRemote come from the agent's own
	// model-state ping (a CanonicalStatusEvent with ModelID set — see
	// handleCanonicalEvent), never from the launch flags: a flag can be
	// defaulted or absent and would lie about what actually resolved. Empty
	// until the first ping arrives, which is the first thing the agent writes
	// after construction — see renderModelChip for the pre-ping fallback.
	modelID      string
	modelDisplay string
	modelBackend string
	modelRemote  bool

	// awaitingModelSwitch is true for the duration of a `/model <id>` turn
	// this session itself started. A model-state ping that disagrees with
	// modelID while this is true is the expected confirmation of THAT
	// switch — no warning. One that disagrees while this is false means the
	// agent process was replaced without anyone here asking for it (a
	// cancelled turn respawns the child from its ORIGINAL launch flags,
	// silently reverting any live switch — see subprocess.go's discard/
	// respawn) — see handleCanonicalEvent. Cleared on the turn's own
	// terminal event, whichever way that turn ended, so a failed switch
	// (Lemonade down, bad credential — no ping ever arrives) never leaves
	// this stuck true.
	awaitingModelSwitch bool

	connected    bool
	totalSteps   int
	initialQuery string
	err          error
	queryStart   time.Time // tracks when the current query started
	firstToken   bool      // whether the first real inference token has arrived this turn (not just any SSE frame)
	ttft         time.Duration

	// followTail is true while the view should stay pinned to the newest
	// content. Scrolling up clears it, so a streaming answer stops yanking the
	// reader back to the bottom mid-sentence; returning to the bottom (or
	// sending a new message) restores it.
	followTail bool

	// pendingPreScan buffers a fetch resolved mid-turn until that turn ends, so it never lands between a question and its reply.
	pendingPreScan json.RawMessage
	// preScanRenderedThisTurn is true once the CURRENT turn's own typed
	// tool_result has drawn the pre-scan card (#2743 checkpoint review).
	// drainPendingPreScan checks it before draining the buffered on-open
	// snapshot: without this, a "triage my inbox" turn that itself
	// produces a fresh card would have that fresh data immediately
	// clobbered by the shallower snapshot the on-open fetch buffered
	// before the turn started.
	preScanRenderedThisTurn bool
}

func NewChatModel(c client.AgentClient, agentName string, initialQuery string, dev bool) ChatModel {
	ti := textarea.New()
	ti.Placeholder = "Ask anything — Enter to send, Alt+Enter for a new line"
	ti.Focus()
	ti.CharLimit = 4096
	ti.SetHeight(1)
	ti.ShowLineNumbers = false

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(theme.Highlight)

	vp := viewport.New(80, 20)
	vp.SetContent("")

	m := ChatModel{
		client:       c,
		agentName:    agentName,
		agentID:      agentName,
		initialQuery: initialQuery,
		dev:          dev,
		input:        ti,
		spinner:      sp,
		viewport:     vp,
		connected:    true,
		followTail:   true,
	}
	// Reads the transport, never a saved preference: bypass and Claude mode
	// are off on a fresh launch unless THIS launch asked on the command line.
	return m.applyLaunchBypass().applyLaunchClaude()
}

// NewChatModelFromHub creates a ChatModel launched from the hub, enabling Esc-to-return behavior.
func NewChatModelFromHub(c client.AgentClient, agentID, agentName string, dev bool) ChatModel {
	m := NewChatModel(c, agentName, "", dev)
	m.agentID = agentID
	m.fromHub = true
	return m
}

// NewChatModelForCatalogAgent creates a standalone ChatModel (esc quits -- see
// CanReturnToHub) for a real catalog agent, so agentID is the catalog id
// rather than NewChatModel's default of the display name.
func NewChatModelForCatalogAgent(c client.AgentClient, agentID, agentName string, dev bool) ChatModel {
	m := NewChatModel(c, agentName, "", dev)
	m.agentID = agentID
	return m
}

// preScanAgentID is the one agent this on-open fetch applies to today.
// Scoped by id rather than by capability alone so a future agent that
// happens to reuse the PreScanFetcher interface for something unrelated
// doesn't unexpectedly get this fetch too.
const preScanAgentID = "email"

// preScanCardIdentity marks the singular inbox pre-scan card (#2743) so
// both entry points that can produce one — the on-open fetch below and a
// typed turn's own tool_result (canonical.go) — update the SAME message in
// place instead of each appending its own. See Message.Identity.
const preScanCardIdentity = "email_prescan"

func (m ChatModel) Init() tea.Cmd {
	cmds := []tea.Cmd{
		m.spinner.Tick,
		textarea.Blink,
	}
	if m.initialQuery != "" {
		cmds = append(cmds, func() tea.Msg {
			return sendQueryMsg{query: m.initialQuery}
		})
		// The on-open pre-scan card (#2743) is gone: it spent a Gmail scan
		// before the user asked for anything, and showed a shallower version
		// of what "triage my inbox" answers properly a moment later. The
		// card still renders when a turn's own pre_scan_inbox result arrives.
	} else if m.dev && m.preScanGateMismatch() {
		// A client that could serve the pre-scan view but an agentID that
		// doesn't match must not fail with no signal at all.
		fmt.Fprintf(os.Stderr,
			"[DEBUG] pre-scan fetch skipped: agentID %q has a PreScanFetcher client but does not match %q\n",
			m.agentID, preScanAgentID)
	}
	return tea.Batch(cmds...)
}

// preScanGateMismatch reports whether m.client could serve the pre-scan
// view even though m.agentID didn't earn the fetch.
func (m ChatModel) preScanGateMismatch() bool {
	if m.agentID == preScanAgentID {
		return false
	}
	_, hasFetcher := m.client.(client.PreScanFetcher)
	return hasFetcher
}

// fetchPreScan builds the Cmd that fetches the email agent's inbox pre-scan
// (#2743). A transport that doesn't implement client.PreScanFetcher
// (subprocess mode has no HTTP relay to ask) is skipped silently — this is
// a best-effort side-channel read, never a requirement for the chat
// surface to function. A peer whose contract predates needs_you degrades
// to an honest notice rather than the confident empty card an unguarded
// decode would produce.
func (m ChatModel) fetchPreScan() tea.Cmd {
	fetcher, ok := m.client.(client.PreScanFetcher)
	if !ok {
		return nil
	}
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		data, err := fetcher.FetchPreScan(ctx)
		if err != nil {
			var tooOld *client.ErrPreScanContractTooOld
			if errors.As(err, &tooOld) {
				return preScanDegradedMsg{notice: tooOld.Error()}
			}
			return preScanFetchFailedMsg{err: err}
		}
		return preScanFetchedMsg{data: data}
	}
}

// upsertCard appends a RoleCard message, or — when identity is non-empty and
// a message already carries it — replaces that message's payload in place
// (#2743). Looked up by identity across the CURRENT m.messages slice on
// every call, never a tracked index: `/clear` sets m.messages to nil
// (see the "/clear" case below), so a stale index would panic or silently
// overwrite an unrelated message. The render cache is cleared so an
// in-place update is never served the stale layout (Message.cardCache is
// otherwise keyed on width alone).
func (m *ChatModel) upsertCard(identity, toolName, render string, data json.RawMessage) {
	if identity != "" {
		for i := range m.messages {
			if m.messages[i].Role == RoleCard && m.messages[i].Identity == identity {
				m.messages[i].ToolName = toolName
				m.messages[i].Render = render
				m.messages[i].Data = data
				m.messages[i].cardCache = ""
				m.messages[i].cardCacheWidth = 0
				return
			}
		}
	}
	m.messages = append(m.messages, Message{
		Role:     RoleCard,
		Identity: identity,
		ToolName: toolName,
		Render:   render,
		Data:     data,
	})
}

// upsertPreScanCard draws or updates-in-place the one pre-scan card for
// this session (#2743). Cross-card duplicate items against OTHER card
// types are resolved at render time (see Message.renderCardDeduped), not
// here.
func (m *ChatModel) upsertPreScanCard(data json.RawMessage) {
	m.upsertCard(preScanCardIdentity, "pre_scan_inbox", "email_pre_scan", data)
}

// drainPendingPreScan appends the buffered on-open pre-scan card now that
// its turn has ended, whichever way it ended — UNLESS the turn's own typed
// tool_result already drew a fresher card this same turn (#2743 checkpoint
// review): draining the buffered snapshot over it would clobber the
// fresher data with a shallower one.
func (m *ChatModel) drainPendingPreScan() {
	if m.pendingPreScan == nil {
		return
	}
	data := m.pendingPreScan
	m.pendingPreScan = nil
	if m.preScanRenderedThisTurn {
		return
	}
	m.upsertPreScanCard(data)
}

// Update dispatches the message, then releases a queued follow-up if that
// dispatch happened to end the turn.
//
// The drain lives here rather than in settleTurn because a turn can end down
// any of nine paths — doneMsg, errMsg, the canonical final/error events, and
// five legacy ones — and each returns its own Cmd directly. Draining in one
// place after the fact is the only version that cannot be forgotten when a
// tenth terminal path is added. settleTurn is *ChatModel and returns nothing,
// so it cannot hand back the send Cmd this needs.
func (m ChatModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	updated, cmd := m.update(msg)

	next, ok := updated.(ChatModel)
	if !ok || next.queued == "" || next.streaming {
		return updated, cmd
	}
	// A question or confirmation still on screen owns the conversation; the
	// queued message waits for the user to deal with it. (Both imply streaming
	// today, so this is belt-and-braces against a future path that clears
	// streaming while leaving a modal up.)
	if next.question != nil || next.confirmation != nil {
		return updated, cmd
	}

	query := next.queued
	next.queued = ""
	sent, sendCmd := next.submit(query)
	return sent, tea.Batch(cmd, sendCmd)
}

func (m ChatModel) update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.KeyMsg:
		return m.handleKey(msg)

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.resize()
		return m, nil

	case sendQueryMsg:
		return m.sendQuery(msg.query)

	case channelReadyMsg:
		m.events = msg.ch
		return m, waitForEvent(m.events)

	case cancelRequestFailedMsg:
		// The ASK to stop the run (client.AgentCanceler.Cancel) could not be
		// delivered — the run itself may still be live and settling on its
		// own. Report it without touching m.streaming/m.cancelPending: only
		// the run's own doneMsg/errMsg gets to decide the composer is free
		// again (#2901) — this failure alone must not silently re-enable
		// Enter into a run that is, for all this client knows, still there.
		m.messages = append(m.messages, Message{
			Role: RoleStatus,
			Content: fmt.Sprintf("[!] could not deliver the cancel request: %v. "+
				"Waiting for the run to finish on its own.", msg.err),
		})
		m.updateViewport()
		return m, nil

	case preScanFetchedMsg:
		if m.streaming {
			// Buffer -- appending now would land the card between this turn's question and its reply.
			m.pendingPreScan = msg.data
			return m, nil
		}
		m.upsertPreScanCard(msg.data)
		m.updateViewport()
		return m, nil

	case preScanFetchFailedMsg:
		// Best-effort side-channel read (#2743) — a failure (no mailbox
		// connected, daemon unreachable, a transient connector error) is
		// worth telling the user about, but it must never block or clutter
		// the surface like a turn-ending error would.
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: fmt.Sprintf("[!] inbox pre-scan unavailable: %v", msg.err),
		})
		m.updateViewport()
		return m, nil

	case preScanDegradedMsg:
		// The peer's contract predates needs_you — an honest notice, never
		// the confident (and wrong) empty card an unguarded decode would
		// have produced (#2743).
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "[!] " + msg.notice,
		})
		m.updateViewport()
		return m, nil

	case eventMsg:
		if m.supersededTurn(msg.ch) {
			return m, nil
		}
		return m.handleEvent(msg.event)

	case doneMsg:
		if m.supersededTurn(msg.ch) {
			return m, nil
		}
		m.streaming = false
		m.question = nil
		m.confirmation = nil
		m.flushBuffer()
		m.activity = nil
		// The channel closing is the settlement signal a cancel was waiting
		// on (#2901) — settleTurn appends the confirmed "cancelled" line only
		// now, once it is actually confirmed rather than merely requested.
		m.settleTurn()
		m.updateViewport()
		return m, nil

	case errMsg:
		if msg.turnSeq == 0 || msg.turnSeq != m.turnSeq {
			// A late failure from an already-abandoned turn — the user has
			// since resent (a new sendQuery minted a fresh turnSeq). Must not
			// stomp the live turn's state (#2901: this is exactly what let a
			// stray errMsg reset a live second turn's cancelFn/cancelPending
			// out from under it, so the next Esc/Ctrl+C fell through to quit).
			//
			// The == 0 check is redundant once a turn has been sent, but not
			// before: a fresh model's own turnSeq is also 0, so without it an
			// errMsg constructed without setting the field (turnSeq's zero
			// value, see its doc comment) would silently pass here on a
			// pre-first-turn model instead of being dropped as invalid
			// (#2912 review).
			return m, nil
		}
		m.streaming = false
		// A pending cancel settles here too — the run ended in an error
		// instead of the clean close doneMsg handles, but either way the
		// composer must not stay hostage to a cancel that will never see its
		// happy-path settlement message (the guards in handleKey key off
		// this alongside m.streaming).
		m.settleTurn()
		m.question = nil
		m.confirmation = nil
		m.err = msg.err
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: sanitizeErrorText(msg.err.Error()),
		})
		m.drainPendingPreScan()
		m.activity = nil
		m.updateViewport()
		return m, nil

	case components.QuestionAnsweredMsg:
		q := m.question
		if q == nil || q.RequestID() != msg.RequestID {
			// A late answer for a question that is no longer up — dropping it is
			// correct, but never silently: the agent moved on.
			return m, nil
		}
		m.messages = append(m.messages, Message{
			Role:    RoleUser,
			Content: q.AnswerLabel(msg.Value),
		})
		m.question = nil
		m.updateViewport()
		return m, m.answerQuestion(msg.RequestID, msg.Value)

	case questionFailedMsg:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: sanitizeErrorText(msg.err.Error()),
		})
		m.updateViewport()
		return m, nil

	case components.ConfirmationTimeoutMsg:
		if m.confirmation == nil {
			// Already resolved (the run's own final/error got there first, the
			// overwhelmingly common case against the current stateless email
			// sidecar) or the turn moved on. Dropping is correct — nothing to warn.
			return m, nil
		}
		c, cmd := m.confirmation.ResolveTimeout(msg)
		m.confirmation = &c
		return m, cmd

	case components.ConfirmationDecidedMsg:
		if m.confirmation == nil || m.confirmation.RunID() != msg.RunID {
			// Stale — a decision for a confirmation that is no longer up (already
			// resolved by the run ending first, or superseded). Drop it, same as a
			// stale question answer.
			return m, nil
		}
		return m.resolveConfirmationDecision(msg)

	case confirmActionResultMsg:
		word := "denied"
		if msg.Approved {
			word = "approved"
		}
		if msg.err != nil {
			m.messages = append(m.messages, Message{
				Role:    RoleError,
				Content: sanitizeErrorText(fmt.Sprintf("could not deliver the %s decision for '%s': %v", word, msg.Action, msg.err)),
			})
		} else {
			m.messages = append(m.messages, Message{
				Role:    RoleStatus,
				Content: fmt.Sprintf("[!] %s decision for '%s' delivered", word, msg.Action),
			})
		}
		m.updateViewport()
		return m, nil

	case clipboardResultMsg:
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: copyHint(msg.label, msg.err),
		})
		m.updateViewport()
		return m, nil

	case tea.MouseMsg:
		// The wheel scrolls the transcript. In an alt-screen app the terminal's
		// own scrollback does not exist, so this and the arrow keys are the only
		// way back to what already happened.
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m.afterScroll(), cmd

	case spinner.TickMsg:
		if m.streaming {
			var cmd tea.Cmd
			m.spinner, cmd = m.spinner.Update(msg)
			cmds = append(cmds, cmd)
			// Re-render viewport to update elapsed time display
			m.updateViewport()
		}
		return m, tea.Batch(cmds...)
	}

	// Not gated on !m.streaming: the cursor has to keep blinking while the agent
	// works, or a composer the user CAN type into looks dead.
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	cmds = append(cmds, cmd)

	return m, tea.Batch(cmds...)
}

func (m ChatModel) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	// A pending confirmation owns the keyboard too, but UNLIKE a question, Esc
	// belongs to it: the issue's contract is "Esc denies", not "Esc cancels the
	// turn". Ctrl+C is still the universal way out.
	if m.confirmation != nil && msg.Type != tea.KeyCtrlC {
		c, cmd := m.confirmation.Update(msg)
		m.confirmation = &c
		m.updateViewport()
		return m, cmd
	}

	// A pending question owns the keyboard: the run is blocked on it, so a
	// keystroke that fell through to the composer would go nowhere. Ctrl+C and
	// Esc still cancel the turn — abandoning a question must stay possible.
	if m.question != nil && msg.Type != tea.KeyCtrlC && msg.Type != tea.KeyEsc {
		q, cmd := m.question.Update(msg)
		m.question = &q
		m.updateViewport()
		return m, cmd
	}

	switch msg.Type {
	case tea.KeyCtrlC:
		if m.streaming && m.cancelFn != nil && !m.cancelPending {
			return m.requestCancel()
		}
		// Same escape hatch as Esc below, deliberately: Ctrl+C's first press
		// here already cancels rather than quitting, so it has already left
		// the "twice to force-quit" terminal idiom -- a second press must stay
		// consistent with that and abort locally rather than discarding the
		// transcript (#2901 review).
		if m.streaming && m.cancelPending {
			return m.forceLocalAbort()
		}
		return m, tea.Quit

	case tea.KeyEsc:
		if m.streaming && m.cancelFn != nil && !m.cancelPending {
			return m.requestCancel()
		}
		// A cancel already asked the server to stop, but cooperative
		// cancellation is only checked at agent-loop step boundaries, so this
		// pending window can run tens of seconds with the composer frozen
		// (worst case the 300s read-idle watchdog, sse.go). A second Esc is
		// the expected reaction to a spinner that keeps spinning, and it must
		// not cost the user their session -- abort the local read instead of
		// falling through to ReturnToHubMsg/tea.Quit below (#2901 review).
		if m.streaming && m.cancelPending {
			return m.forceLocalAbort()
		}
		if m.fromHub {
			return m, func() tea.Msg {
				return ReturnToHubMsg{AgentID: m.agentID}
			}
		}
		// Idle, with nowhere to go back to. This used to quit, which made Esc
		// an unadvertised one-keystroke way to destroy the session — on the key
		// people press to mean "never mind", and most reachable in the seconds
		// after a cancelled turn, when pressing it again is the documented
		// escape hatch right up until the turn settles. It now means what it
		// means everywhere else: discard what is in the composer. Ctrl+C is the
		// way out, which is what the status bar has always promised (#2932).
		m.input.Reset()
		m.syncComposerHeight()
		return m, nil

	case tea.KeyCtrlJ:
		// Ctrl+J is the portable newline. Most terminals send a bare CR for
		// Shift+Enter, indistinguishable from Enter, so the only reliable
		// multi-line keys are this and Alt+Enter below.
		m.input.InsertString("\n")
		m.syncComposerHeight()
		return m, nil

	case tea.KeyEnter:
		if msg.Alt {
			// Alt+Enter used to be swallowed outright — the one key a user is
			// most likely to try for a second line did nothing at all.
			m.input.InsertString("\n")
			m.syncComposerHeight()
			return m, nil
		}
		query := strings.TrimSpace(m.input.Value())
		if query == "" {
			return m, nil
		}
		m.input.Reset()
		m.syncComposerHeight()

		// The agent is still working: hold this one rather than dropping the
		// keystroke on the floor. Update sends it the moment the turn settles.
		// Slash commands queue too — /clear typed mid-turn should clear once
		// the turn it belongs to is actually over, not silently do nothing.
		if m.streaming {
			m.queued = query
			m.updateViewport()
			return m, nil
		}

		return m.submit(query)

	case tea.KeyCtrlY:
		// Mouse reporting is on so the wheel can scroll, which is exactly what
		// breaks the terminal's own click-drag selection. Without a copy key
		// the answer is trapped on screen.
		return m, copyToClipboard(m.lastAnswer(), "answer")

	case tea.KeyCtrlB:
		if block := lastCodeBlock(m.lastAnswer()); block != "" {
			return m, copyToClipboard(block, "code block")
		}
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "no code block in the last answer",
		})
		m.updateViewport()
		return m, nil

	case tea.KeyPgUp:
		m.viewport.HalfViewUp()
		return m.afterScroll(), nil

	case tea.KeyPgDown:
		m.viewport.HalfViewDown()
		return m.afterScroll(), nil

	case tea.KeyUp:
		// The composer is one line high, so the arrows have no job there and
		// belong to the transcript — which is where a reader reaches first.
		m.viewport.LineUp(1)
		return m.afterScroll(), nil

	case tea.KeyDown:
		m.viewport.LineDown(1)
		return m.afterScroll(), nil

	case tea.KeyHome, tea.KeyEnd:
		// Home/End belong to whatever the user is working in. Mid-sentence they
		// are cursor keys; with an empty composer there is no cursor to move, so
		// they jump the transcript instead.
		if strings.TrimSpace(m.input.Value()) != "" {
			break
		}
		if msg.Type == tea.KeyHome {
			m.viewport.GotoTop()
		} else {
			m.viewport.GotoBottom()
		}
		return m.afterScroll(), nil
	}

	// Typing is allowed while the agent works — see ChatModel.queued. Enter is
	// intercepted above; everything else composes as normal.
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	// Backspacing away a line has to give the row back, not just adding one.
	m.syncComposerHeight()
	return m, cmd
}

// requestCancel begins cancelling the in-flight turn (Esc / Ctrl+C).
//
// The first version of this fix (#2901) blocked Enter on doneMsg — the run's
// event channel closing — reasoning that was the one locally-observable
// settlement signal. A live run against the real daemon proved that wrong:
// cancelFn (the caller's own context.CancelFunc) tears down THIS client's
// read of the SSE stream, and that local abort is what closed the channel —
// well before the daemon's session run_lock was actually released by the
// sidecar's worker thread, which notices a cancel only cooperatively, at the
// next agent-loop step boundary (see
// hub/agents/email/python/gaia_agent_email/query_routes.py). Calling
// cancelFn() here reproduced the exact race it was meant to close: 5/5
// cancel-then-resend attempts still hit a bare-lock 409 downstream.
//
// The fix is to stop manufacturing that local "done" at all. For a transport
// that implements client.AgentCanceler (the daemon relay), Cancel() asks the
// SERVER to stop the run out of band, while THIS call leaves m.cancelFn /
// m.events untouched — the existing read keeps running, so it can observe
// whatever terminal signal the turn actually produces. In practice that is
// almost always the turn's own CanonicalFinalEvent/CanonicalErrorEvent, NOT
// the channel closing: the server's cooperative cancel just returns an
// ordinary "stopped" answer over the still-open stream, and those handlers
// stop rescheduling waitForEvent the moment they fire, so doneMsg can never
// arrive for them (see settleTurn's doc comment). doneMsg only settles a
// transport where the channel closing IS the terminal signal. Either way,
// settleTurn() is the single place that clears cancelPending once the turn's
// OWN signal — not this call — says it is over.
//
// A transport with no such server-side lock (e.g. a local subprocess) does
// not implement AgentCanceler; for it, tearing down the local connection IS
// the whole cancellation, so the old immediate cancelFn() behavior is exactly
// right and unchanged below.
func (m ChatModel) requestCancel() (tea.Model, tea.Cmd) {
	m.cancelPending = true
	m.activity = nil
	m.question = nil
	m.confirmation = nil
	// A follow-up queued behind this turn was written on the assumption the
	// turn would finish. Stopping the turn stops what was waiting on it —
	// firing it anyway is the opposite of what Esc means. It stays in the
	// composer so nothing typed is lost.
	m.restoreQueuedToComposer()
	m.messages = append(m.messages, Message{
		Role:    RoleStatus,
		Content: cancellingNotice + " (the agent stops at its next step — press again to stop waiting)",
	})
	m.drainPendingPreScan()
	m.updateViewport()

	if canceler, ok := m.client.(client.AgentCanceler); ok {
		return m, func() tea.Msg {
			ctx, cancel := context.WithTimeout(context.Background(), answerTimeout)
			defer cancel()
			if err := canceler.Cancel(ctx); err != nil {
				return cancelRequestFailedMsg{err: err}
			}
			return nil
		}
	}

	m.cancelFn()
	m.cancelFn = nil
	return m, nil
}

// forceLocalAbort is the escape hatch for a SECOND Esc/Ctrl+C pressed while a
// cancel is already pending (#2912 review). Cooperative cancellation is only
// checked at agent-loop step boundaries, so the pending window opened by
// requestCancel can run tens of seconds — worst case the 300s read-idle
// watchdog (sse.go) — with the composer frozen and keystrokes dropped. A
// user pressing the key again because nothing visibly happened must not lose
// the whole session to tea.Quit: this tears the local read down immediately
// (the pre-#2901 per-key behavior) and frees the composer, while warning
// that the run may still be finishing server-side — a following Enter can
// legitimately land on the actionable 409 the AgentCanceler branch above
// already produces a clear message for.
func (m ChatModel) forceLocalAbort() (tea.Model, tea.Cmd) {
	if m.cancelFn != nil {
		m.cancelFn()
	}
	m.cancelFn = nil
	m.events = nil
	m.streaming = false
	m.cancelPending = false
	m.question = nil
	m.confirmation = nil
	m.activity = nil
	// Same reasoning as requestCancel: what was queued behind this turn was
	// written expecting it to finish. Give it back rather than sending it.
	m.restoreQueuedToComposer()
	m.messages = append(m.messages, Message{
		Role: RoleStatus,
		Content: "gave up waiting locally — the run may still be finishing on the server; " +
			"a retry may briefly answer \"already in progress\"",
	})
	m.updateViewport()
	return m, nil
}

// restoreQueuedToComposer puts a queued follow-up back where the user typed it,
// so abandoning a turn never silently eats the sentence they were holding.
// Anything already half-typed in the composer wins — that is the newer thought.
func (m *ChatModel) restoreQueuedToComposer() {
	if m.queued == "" {
		return
	}
	if strings.TrimSpace(m.input.Value()) == "" {
		m.input.SetValue(m.queued)
		m.input.CursorEnd()
		m.syncComposerHeight()
	}
	m.queued = ""
}

// submit routes one composed line: a slash command runs locally, anything else
// becomes a turn. Both the composer and the queue drain go through here, so a
// "/clear" typed mid-turn still clears when it finally lands instead of being
// sent to the agent as a literal question.
func (m ChatModel) submit(query string) (tea.Model, tea.Cmd) {
	// `/model` takes a free-form argument (a model id), so it can't join the
	// exact-match switch below like /bypass's fixed variants — it's dispatched
	// here instead, still before anything falls through to sendQuery.
	if isModelCommand(query) {
		if !m.supportsModelCommand() {
			// Same shape as setBypass's capability check (bypass.go): refuse
			// visibly rather than let the literal text ship as a chat
			// question the agent has no way to understand.
			m.messages = append(m.messages, Message{
				Role: RoleError,
				Content: m.agentName + " does not support live model switching " +
					"(/model) — only the gaia flagship agent does.",
			})
			m.updateViewport()
			return m, nil
		}
		// A control request, not a question — unlike sendQuery, this never
		// posts a user chat bubble; the agent's own confirmation (or
		// refusal) is the only line that belongs in the transcript. Still
		// rides the real query channel (startTurn/Send), not the
		// fire-and-forget control one /bypass uses — see
		// gaia_agent.stdio.run_model_command for why.
		m.awaitingModelSwitch = true
		return m.startTurn(query)
	}

	switch query {
	case "/help":
		return m, func() tea.Msg { return ToggleHelpMsg{} }

	case "/hub":
		if m.fromHub {
			return m, func() tea.Msg {
				return ReturnToHubMsg{AgentID: m.agentID}
			}
		}
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "Not launched from hub. Use Ctrl+C to quit.",
		})
		m.updateViewport()
		return m, nil

	case "/clear":
		m.messages = nil
		// Daemon-transport agents are stateless per turn: the host pushes the
		// transcript back as `context`, so clearing the view must clear that
		// too or the "cleared" history keeps being sent.
		if r, ok := m.client.(client.TranscriptResetter); ok {
			r.ResetTranscript()
		}
		m.updateViewport()
		return m, nil

	case "/bypass":
		if m.bypassPermissions {
			return m.setBypass(false)
		}
		return m.armBypass()

	case "/bypass on":
		if m.bypassPermissions {
			return m.bypassNote("Bypass permissions is already ON."), nil
		}
		return m.armBypass()

	case "/bypass confirm":
		if !m.bypassArmed {
			return m.bypassNote("Nothing to confirm. Type /bypass first — it " +
				"explains what you would be turning on."), nil
		}
		return m.setBypass(true)

	case "/bypass off":
		if !m.bypassPermissions {
			return m.bypassNote("Bypass permissions is already off."), nil
		}
		return m.setBypass(false)
	}

	return m.sendQuery(query)
}

func (m ChatModel) sendQuery(query string) (tea.Model, tea.Cmd) {
	m.messages = append(m.messages, Message{
		Role:    RoleUser,
		Content: query,
	})
	return m.startTurn(query)
}

// startTurn is sendQuery's shared machinery: everything after "what goes in
// the transcript" is identical whether the line is a real question or a
// `/model` command.
func (m ChatModel) startTurn(query string) (tea.Model, tea.Cmd) {
	m.streaming = true
	m.activity = nil
	m.buffer = ""
	// Asking a new question means you want to see its answer, wherever the
	// scroll happened to be left.
	m.followTail = true
	m.queryStart = time.Now()
	m.firstToken = false
	m.ttft = 0
	// Per-turn, like ttft above it. Left standing, a turn whose `final` carries
	// no usage.steps reported the PREVIOUS turn's count as its own.
	m.totalSteps = 0
	// A new turn starts having drawn no card yet -- see drainPendingPreScan.
	m.preScanRenderedThisTurn = false
	m.updateViewport()

	ctx, cancel := context.WithCancel(context.Background())
	m.cancelFn = cancel

	m.turnSeq++
	seq := m.turnSeq

	c := m.client
	return m, tea.Batch(
		m.spinner.Tick,
		func() tea.Msg {
			ch, err := c.Send(ctx, query)
			if err != nil {
				return errMsg{err: err, turnSeq: seq}
			}
			return channelReadyMsg{ch: ch}
		},
	)
}

func waitForEvent(ch <-chan interface{}) tea.Cmd {
	return func() tea.Msg {
		if ch == nil {
			return doneMsg{}
		}
		evt, ok := <-ch
		if !ok {
			return doneMsg{ch: ch}
		}
		return eventMsg{ch: ch, event: evt}
	}
}

// supersededTurn reports whether a message belongs to a turn that is no longer
// the current one, so it must be ignored rather than allowed to end the live turn.
func (m ChatModel) supersededTurn(ch <-chan interface{}) bool {
	return ch != nil && ch != m.events
}

// settleTurn clears the per-turn cancellation bookkeeping once THIS turn's
// own terminal signal proves it is over: doneMsg/errMsg (already scoped to
// the live turn above) or a terminal canonical/legacy event
// (CanonicalFinalEvent, CanonicalErrorEvent, AnswerEvent, DoneEvent,
// AgentErrorEvent, legacy ErrorEvent, StatusEvent{complete}). Every caller
// already knows the signal belongs to the current turn — eventMsg's own
// dispatch in Update ran supersededTurn before handleEvent ever saw the
// event — so it is always safe to clear m.cancelFn/m.events here.
//
// Without this, a cancelled turn that settles via its own terminal event —
// the ONLY way a daemon-relay turn ever settles, since CanonicalFinalEvent
// and CanonicalErrorEvent stop rescheduling waitForEvent the moment they
// fire (correctly: no more events are expected), so doneMsg can never arrive
// for them — left cancelPending stuck true for the rest of the session. The
// next Esc/Ctrl+C's `!m.cancelPending` guard then permanently failed and
// fell through to tea.Quit instead of cancelling (#2901 second-cycle crash,
// reproduced live 3/3 against the real daemon).
func (m *ChatModel) settleTurn() {
	m.events = nil
	m.cancelFn = nil
	// A failed `/model` switch (Lemonade down, bad credential) never sends a
	// model-state ping — clearing here, not just on the ping itself, is what
	// keeps a failure from leaving this stuck true and permanently
	// suppressing the revert-warning in handleCanonicalEvent.
	m.awaitingModelSwitch = false
	// "cancelling…" describes a request that is in flight, so it must not
	// outlive it. Left in place it became a permanent claim in the scrollback —
	// and when the cancel lost the race it sat directly above the answer that
	// did arrive, contradicting it.
	m.dropCancellingNotice()
	if m.cancelPending {
		m.cancelPending = false
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "cancelled",
		})
	}
}

// cancellingNotice is the transient line requestCancel shows while a cancel is
// in flight. Matched by prefix so its parenthetical can be reworded freely.
const cancellingNotice = "cancelling…"

// dropCancellingNotice removes that transient line from the transcript.
//
// Scanned from the end rather than assuming it is last: an answer, tool result
// or error can land after the cancel was requested but before the turn settles
// — which is precisely the case where leaving it in reads as the transcript
// contradicting itself.
func (m *ChatModel) dropCancellingNotice() {
	for i := len(m.messages) - 1; i >= 0; i-- {
		msg := m.messages[i]
		if msg.Role == RoleStatus && strings.HasPrefix(msg.Content, cancellingNotice) {
			m.messages = append(m.messages[:i], m.messages[i+1:]...)
			return
		}
	}
}

// CancelActiveTurn stops any in-flight turn. The UI owns the per-turn context, so
// tearing this view down has to cancel it — otherwise the transport keeps
// streaming into a screen nobody is watching and the agent run stays alive.
func (m *ChatModel) CancelActiveTurn() {
	if m.cancelFn != nil {
		m.cancelFn()
		m.cancelFn = nil
	}
	m.streaming = false
	m.events = nil
	m.question = nil
	m.confirmation = nil
}

func (m ChatModel) handleEvent(evt interface{}) (tea.Model, tea.Cmd) {
	// TTFT is anchored on the first real inference token (CanonicalTokenEvent
	// / legacy ChunkEvent below), not on the first SSE frame of any kind — a
	// turn-start status frame arrives in single-digit ms and would otherwise
	// make ttft measure "server said hello", not "model produced text" (#2899).

	// The daemon transport speaks the canonical seven-event contract; the
	// subprocess transport speaks the legacy in-process vocabulary below.
	if updated, cmd, handled := m.handleCanonicalEvent(evt); handled {
		return updated, cmd
	}

	switch e := evt.(type) {
	case event.ThinkingEvent:
		m.activity = append(m.activity, ActivityItem{
			Kind:    "thinking",
			Content: e.Content,
		})

	case event.ToolStartEvent:
		m.activity = append(m.activity, ActivityItem{
			Kind:    "tool",
			Content: e.Tool,
		})

	case event.ToolArgsEvent:
		if len(m.activity) > 0 {
			last := &m.activity[len(m.activity)-1]
			if last.Kind == "tool" {
				// Try to extract a clean command from the args JSON
				argStr := extractCommandFromArgs(e.Args)
				if argStr != "" {
					last.Content = e.Tool + ": " + argStr
				}
			}
		}

	case event.ToolResultEvent:
		summary := e.Summary
		if summary == "" {
			summary = e.Title
		}
		// clean before truncateRunes, not strings.ReplaceAll after: stdout
		// arrives with tabs, CRs and occasionally ANSI colour, and the old
		// byte slice at [:60] cut mid-rune on any non-ASCII output — a path
		// with an accent or a box-drawing character rendered as mojibake.
		summary = truncateRunes(clean(summary), 60)
		if len(m.activity) > 0 {
			last := &m.activity[len(m.activity)-1]
			if last.Kind == "tool" {
				last.Done = true
				last.Success = &e.Success
				if summary != "" {
					last.Content += " → " + summary
				}
			}
		}

	case event.ToolEndEvent:
		if len(m.activity) > 0 {
			last := &m.activity[len(m.activity)-1]
			if last.Kind == "tool" && !last.Done {
				last.Done = true
				last.Success = &e.Success
			}
		}

	case event.StepEvent:
		// Tracked, not shown. "Step 2/50" is the agent loop's bound, not the
		// user's progress — it says neither what is happening nor how far along
		// the work is, and it pushed the informative tool line off the screen.
		m.totalSteps = e.Step

	case event.StatusEvent:
		if e.Status == "complete" {
			m.flushBuffer()
			m.streaming = false
			m.activity = nil
			m.settleTurn()
			m.updateViewport()
			return m, nil
		}
		// Filter out redundant status messages that duplicate thinking/tool events
		msg := e.Message
		if msg == "Thinking" || strings.HasPrefix(msg, "Executing ") {
			// Already shown by ThinkingEvent/ToolStartEvent — skip
		} else if msg != "" {
			m.activity = append(m.activity, ActivityItem{
				Kind:    "status",
				Content: msg,
			})
		}

	case event.AnswerEvent:
		m.flushBuffer()
		duration := time.Since(m.queryStart)
		rendered := components.RenderMarkdown(e.Content)
		m.messages = append(m.messages, Message{
			Role:      RoleAssistant,
			Content:   e.Content,
			Rendered:  rendered,
			Duration:  duration,
			TTFT:      m.ttft,
			Steps:     e.Steps,
			ToolsUsed: e.ToolsUsed,
		})
		m.streaming = false
		m.activity = nil
		m.totalSteps = e.Steps
		m.settleTurn()
		m.updateViewport()
		return m, nil

	case event.ChunkEvent:
		if !m.firstToken {
			m.firstToken = true
			m.ttft = time.Since(m.queryStart)
		}
		m.buffer += e.Content

	case event.AgentErrorEvent:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: sanitizeErrorText(e.Content),
		})
		m.streaming = false
		m.activity = nil
		m.settleTurn()
		m.updateViewport()
		return m, nil

	case event.ErrorEvent:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: sanitizeErrorText(e.Content),
		})
		m.streaming = false
		m.activity = nil
		m.settleTurn()
		m.updateViewport()
		return m, nil

	case event.DoneEvent:
		m.flushBuffer()
		m.streaming = false
		m.activity = nil
		m.settleTurn()
		m.updateViewport()
		return m, nil
	}

	m.updateViewport()
	return m, waitForEvent(m.events)
}

func (m *ChatModel) flushBuffer() {
	content := m.buffer
	if content == "" {
		return
	}
	rendered := components.RenderMarkdown(content)
	m.messages = append(m.messages, Message{
		Role:     RoleAssistant,
		Content:  content,
		Rendered: rendered,
	})
	m.buffer = ""
}

// composerMaxRows caps how tall the composer grows. Past this it scrolls
// internally: a composer that can eat the whole window hides the conversation
// the user is writing about.
const composerMaxRows = 6

// composerRows is the height the composer wants right now — one row per line
// the user has actually written.
func (m ChatModel) composerRows() int {
	rows := m.input.LineCount()
	if rows < 1 {
		rows = 1
	}
	if rows > composerMaxRows {
		rows = composerMaxRows
	}
	return rows
}

// syncComposerHeight grows or shrinks the composer to fit what has been typed,
// re-laying out the pane above it when the height actually changes. Called
// after every keystroke, so it must be cheap when nothing moved.
func (m *ChatModel) syncComposerHeight() {
	want := m.composerRows()
	if m.input.Height() == want {
		return
	}
	m.input.SetHeight(want)
	m.resize()
}

func (m *ChatModel) resize() {
	headerH := 1
	statusH := 1
	inputH := m.composerRows() + 2
	padding := 2

	vpHeight := m.height - headerH - statusH - inputH - padding
	if vpHeight < 1 {
		vpHeight = 1
	}
	vpWidth := m.width
	if vpWidth < 10 {
		vpWidth = 10
	}

	m.viewport.Width = vpWidth
	m.viewport.Height = vpHeight
	m.input.SetWidth(vpWidth - 2)

	// Markdown wraps to the same measure the answer is laid out at, or glamour
	// hard-wraps at a different column than the panel and the block develops a
	// ragged second edge.
	components.SetWordWrap(m.answerWidth() - 2)
	if m.question != nil {
		m.question.SetWidth(m.cardWidth())
	}
	if m.confirmation != nil {
		m.confirmation.SetWidth(m.cardWidth())
	}
	m.updateViewport()
}

// afterScroll records whether the reader is still at the newest content. Once
// they scroll away, streamed tokens stop dragging the view back down; landing on
// the bottom again re-arms the follow.
func (m ChatModel) afterScroll() ChatModel {
	m.followTail = m.viewport.AtBottom()
	return m
}

func (m *ChatModel) updateViewport() {
	var sb strings.Builder

	// Show welcome message if no messages yet
	if len(m.messages) == 0 && !m.streaming {
		sb.WriteString(m.renderWelcome())
		sb.WriteString("\n")
	}

	// seen accumulates message_ids across cards within one turn so a second
	// card doesn't redraw an item its turn's first card already showed. It
	// resets at each RoleUser message: a new turn's mail can legitimately
	// repeat an id an earlier turn's card already rendered (still urgent on
	// the next scan is not a duplicate), so dedup must not span turns.
	seen := make(map[string]bool)
	for i := range m.messages {
		if m.messages[i].Role == RoleUser {
			seen = make(map[string]bool)
			// A blank line ahead of every turn but the first. Without it the
			// transcript is one unbroken block and the eye has nothing to
			// anchor on when scrolling back for "where did I ask that?".
			if sb.Len() > 0 {
				sb.WriteString("\n")
			}
		}
		// By index, not by value: rendering a card memoizes onto the message.
		sb.WriteString(m.renderMessage(&m.messages[i], seen))
		sb.WriteString("\n")
		if spacedAfter(m.messages[i].Role) {
			sb.WriteString("\n")
		}
	}

	// The live region appears the moment a turn starts, not once the first tool
	// lands — the silent gap before an agent's first event is exactly when a
	// blank screen reads as a hang.
	if m.streaming {
		sb.WriteString(m.renderLiveRegion())
		sb.WriteString("\n")
	}

	if m.confirmation != nil {
		sb.WriteString(m.confirmation.View())
		sb.WriteString("\n")
	}

	if m.question != nil {
		sb.WriteString(m.question.View())
		sb.WriteString("\n")
	}

	// The answer as it arrives, laid out exactly where the finished one will be
	// — same indent, same wrap — so the text does not jump when `final` replaces
	// the streamed tokens with the authoritative copy.
	if buf := m.buffer; m.streaming && buf != "" {
		sb.WriteString(answerPanelStyle.Width(m.answerWidth()).Render(buf))
		sb.WriteString("\n")
	}

	m.viewport.SetContent(sb.String())
	if m.followTail {
		m.viewport.GotoBottom()
	}
}

func (m ChatModel) renderWelcome() string {
	title := lipgloss.NewStyle().
		Bold(true).
		Foreground(theme.AccentBright).
		Render("Welcome to GAIA")

	hint := activityStyle.Render("Ask a question, or type /help for what else this can do.")

	// "Connected to: GAIA" under "Welcome to GAIA" is the same word twice; the
	// line only earns its place when a DIFFERENT agent is on the other end.
	if isBrandName(m.agentName) {
		return title + "\n\n" + hint
	}
	agent := lipgloss.NewStyle().
		Foreground(theme.Text).
		Render("Connected to: " + m.agentName)
	return title + "\n" + agent + "\n\n" + hint
}

// cardWidth is the outer width a render card may occupy. The viewport keeps a
// couple of columns for its own gutter, so a card sized to the raw terminal
// width wraps and the borders shear. It never exceeds the viewport itself —
// a card wider than the window it lives in is the same shear by another route.
func (m ChatModel) cardWidth() int {
	w := m.width - 4
	if w > m.viewport.Width && m.viewport.Width > 0 {
		w = m.viewport.Width
	}
	if w < 1 {
		w = 1
	}
	return w
}

// answerMeasure caps how wide a line of prose gets. A 200-column terminal will
// happily lay an answer out as 200-character lines, and the eye loses the start
// of the next one on the way back — the reason newspapers set narrow columns.
// Tables and cards are not prose and are not capped by this.
const answerMeasure = 88

// answerWidth is the width an answer lays out to — the same for the streaming
// copy and the finished one, so text never reflows when `final` lands.
func (m ChatModel) answerWidth() int {
	w := m.width - 4
	if w > answerMeasure {
		w = answerMeasure
	}
	if w < 20 {
		w = 20
	}
	return w
}

// wrapForPane wraps text to the visible pane, leaving it untouched before the
// first WindowSizeMsg (when no width is known yet).
func (m ChatModel) wrapForPane(s string) string {
	if m.width <= 0 {
		return s
	}
	return components.WrapText(s, m.cardWidth())
}

// wrapProse wraps to the same measure an answer lays out at, so prose from
// either side of the conversation shares one column.
func (m ChatModel) wrapProse(s string) string {
	if m.width <= 0 {
		return s
	}
	return components.WrapText(s, m.answerWidth())
}

// spacedAfter reports whether a message gets a blank line under it. Substantial
// blocks — a question, an answer, a card, an error — get air; consecutive status
// notes stay tight, since spreading a run of one-liners apart makes them read as
// separate events rather than one aside.
func spacedAfter(role MessageRole) bool {
	switch role {
	case RoleUser, RoleAssistant, RoleCard, RoleError:
		return true
	}
	return false
}

// answerStats is the footnote under a finished answer.
//
// By default it is one number: how long that took. Everything else —
// time-to-first-token, token count, tokens/sec, agent-loop step count, tool
// count — is harness telemetry. It measures the machinery, not the answer, and
// printing six metrics under every reply trains the eye to skip the whole line,
// including the one figure a person actually reads. --dev turns the full
// breakdown back on for the people tuning the machinery.
func (m ChatModel) answerStats(msg *Message) string {
	if msg.Duration <= 0 {
		return ""
	}
	stats := []string{fmt.Sprintf("%.1fs", msg.Duration.Seconds())}
	if !m.dev {
		return stats[0]
	}

	if msg.TTFT > 0 {
		stats = append(stats, fmt.Sprintf("ttft %.1fs", msg.TTFT.Seconds()))
	}
	// Real generated-token count (#2899) — no longer a char-count guess. Zero
	// means the sidecar didn't report usage.tokens (the legacy transport, or an
	// older agent); omit rather than fall back to the old estimate, which would
	// silently reintroduce the exact bug that replaced.
	if msg.Tokens > 0 {
		stats = append(stats, fmt.Sprintf("%d tokens", msg.Tokens))
		if inferTime := msg.Duration - msg.TTFT; inferTime > 0 {
			stats = append(stats, fmt.Sprintf("%.1f tok/s", float64(msg.Tokens)/inferTime.Seconds()))
		}
	}
	if msg.Steps > 0 {
		stats = append(stats, fmt.Sprintf("%d steps", msg.Steps))
	}
	if msg.ToolsUsed > 0 {
		stats = append(stats, fmt.Sprintf("%d tools", msg.ToolsUsed))
	}
	return strings.Join(stats, " · ")
}

// renderMessage draws one message. seen threads cross-card dedup for the
// RoleCard case (see Message.renderCardDeduped); pass nil for a standalone
// render with no dedup.
func (m ChatModel) renderMessage(msg *Message, seen map[string]bool) string {
	switch msg.Role {
	case RoleUser:
		// The WHOLE line is dimmed, prefix and text alike. Styling only the
		// "You:" label left the question itself at the terminal's default
		// foreground — the brightest thing on screen, competing with the answer
		// it was asking for. A free-text answer to a mid-run question lands here
		// too and can be long, so it wraps.
		//
		// Wrapped to the ANSWER's measure, not the pane's: a question and its
		// answer read as one exchange only if they share a left AND a right
		// edge. On a 200-column terminal the pane measure ran the question out
		// to 196 columns above an answer capped at 88, so the pair looked like
		// two unrelated blocks.
		return userStyle.Render(m.wrapProse("▶ You: " + msg.Content))

	case RoleAssistant:
		content := msg.Content
		if msg.Rendered != "" {
			content = msg.Rendered
		}
		panel := answerPanelStyle.Width(m.answerWidth()).Render(content)

		if stats := m.answerStats(msg); stats != "" {
			panel += "\n" + activityStyle.Render("  "+stats)
		}
		return panel

	case RoleCard:
		return msg.renderCardDeduped(m.cardWidth(), seen)

	case RoleError:
		panelWidth := m.width - 4
		if panelWidth < 20 {
			panelWidth = 20
		}
		return errorPanelStyle.Width(panelWidth).Render("[!] " + msg.Content)

	case RoleStatus:
		// Wrapped, not clipped: the viewport does not soft-wrap, so a status
		// line longer than the pane loses its tail — and for the ones that
		// carry a remedy, the tail IS the remedy.
		return statusMsgStyle.Render(m.wrapForPane("  " + msg.Content))

	default:
		return msg.Content
	}
}

// workLogLines caps how many ACTIONS the live work log keeps. Bounded so a long
// turn cannot push the transcript off screen, deep enough that repeated tool
// calls read as progress.
const workLogLines = 6

// workLogMaxRows caps the log's rendered HEIGHT. An action can occupy two rows
// once its outcome lands, so the action cap alone would let the region grow to
// 13 rows and shove the answer the user is reading off the top.
const workLogMaxRows = 9

// logRows is the height budget for THIS terminal: never more than half the
// visible pane, so the log cannot crowd out the transcript on a short window
// (a 12-row terminal leaves the viewport 5 rows — the fixed cap alone would
// fill it and then some).
func (m ChatModel) logRows() int {
	budget := workLogMaxRows
	if h := m.viewport.Height; h > 0 && h/2 < budget {
		budget = h / 2
	}
	if budget < 2 {
		budget = 2
	}
	return budget
}

// logWidth is the column budget for one work-log line on THIS terminal. The
// fixed caps are a readability ceiling, not a layout assumption: below ~80
// columns every line would soft-wrap and double the region's real height.
func (m ChatModel) logWidth() int {
	// 4 for the marker gutter, 2 for the viewport's own edge.
	w := m.width - 6
	if w > narrationWidth {
		w = narrationWidth
	}
	if w < 16 {
		w = 16
	}
	return w
}

// stillWorkingAfter is when the live region starts saying the wait is expected.
// A local 4B model routinely takes 60-90s on an inbox triage; without this line
// the user's next move is ctrl+c.
const stillWorkingAfter = 20 * time.Second

// Work-log glyphs, all width-1 and none of them emoji.
//
// The first cut used ⚒ (U+2692), which carries Emoji=Yes: a terminal with an
// emoji font renders it DOUBLE width while ansi.StringWidth still reports 1, so
// every tool line overruns its budget by a column. ▪ (U+25AA), ✻ (U+273B) and
// └ carry no emoji presentation and measure 1 everywhere. State is never carried
// by a glyph or a colour anyway — a failed call says "failed" in words on its
// own outcome line.
const (
	glyphTool   = "▪"
	glyphStatus = "✻"
	glyphDetail = "└"
)

// renderLiveRegion draws the rolling activity log for the running turn: one line
// per meaningful action, newest last, each closed action followed by a single
// indented outcome line.
//
// The spinner and the elapsed clock ride on the LAST line — the thing happening
// right now — rather than sitting in a header of their own. A timer with no
// description next to it is the "Working 0:29 / connecting..." screen this
// replaced: it proves the process is alive and says nothing about what it is
// doing (#2804).
func (m ChatModel) renderLiveRegion() string {
	elapsed := time.Since(m.queryStart)

	log := collapseActivity(m.activity)
	if len(log) > workLogLines {
		log = log[len(log)-workLogLines:]
	}

	// The live slot is the last still-open action. A finished one cannot be it:
	// the agent has moved on to something this client has no event for yet.
	live := -1
	if n := len(log); n > 0 && !log[n-1].Done {
		live = n - 1
	}

	// Rendered per action, so the height cap can drop whole actions. Trimming
	// raw lines instead would leave a `└` outcome line orphaned at the top,
	// hanging under nothing.
	groups := make([][]string, 0, len(log)+1)
	for i, item := range log {
		groups = append(groups, m.renderActivityItem(item, i == live, elapsed))
	}
	if live < 0 {
		groups = append(groups, []string{m.renderLiveLine(m.idlePhrase(len(log)), elapsed)})
	}
	// Shown until something has actually COMPLETED. A stage line alone does not
	// count: a turn that sits on "Working out how to answer" for 1:47 with no
	// tool result under it is precisely the wait that needs saying is normal.
	// Measured against the WHOLE turn, not the trimmed window, or the hint
	// reappears the moment the last finished action scrolls out of view.
	hint := ""
	if !anyCompleted(m.activity) && elapsed >= stillWorkingAfter {
		hint = "     " + activityStyle.Render(glyphDetail+" still working — local model, usually 60-90s")
	}

	// The hint is part of the height budget, not an extra row bolted on after
	// it — counted here so the region can never exceed logRows().
	budget := m.logRows()
	if hint != "" {
		budget--
	}
	// Oldest first: the newest action is the one being watched, and the live
	// line is always last.
	rows := 0
	for i := len(groups) - 1; i >= 0; i-- {
		rows += len(groups[i])
		if rows > budget {
			groups = groups[i+1:]
			break
		}
	}

	var lines []string
	for _, g := range groups {
		lines = append(lines, g...)
	}
	if hint != "" {
		lines = append(lines, hint)
	}

	return strings.Join(lines, "\n")
}

// anyCompleted reports whether anything in the log has actually finished — the
// difference between "the agent says it is thinking" and "the agent has done
// something".
func anyCompleted(log []ActivityItem) bool {
	for _, item := range log {
		if item.Done {
			return true
		}
	}
	return false
}

// idlePhrase describes the turn when no tool call is open — the model is either
// working out what to do or writing the answer. Both are real states worth
// naming; "Waiting for agent" names neither.
// Deliberately not phrased like the sidecar's own status text: this line sits
// directly under it, and two lines saying the same thing read as a stuck loop.
func (m ChatModel) idlePhrase(logLen int) string {
	switch {
	case m.cancelPending:
		// requestCancel clears the activity log but leaves the turn streaming,
		// so without this the spinner sat under "cancelling…" cheerfully
		// announcing "Getting started".
		return "Stopping at the next step"
	case m.buffer != "":
		return "Writing your answer"
	case logLen == 0:
		return "Getting started"
	default:
		return "Thinking about the next step"
	}
}

// renderLiveLine draws the one line that owns the spinner and the clock.
func (m ChatModel) renderLiveLine(content string, elapsed time.Duration) string {
	return "  " + m.spinner.View() + " " + thinkingStyle.Render(content) + "  " +
		activityStyle.Render(formatElapsed(elapsed))
}

// collapseActivity drops step markers (the header carries the current one) and
// folds runs of the same tool into "name xN", so a triage that calls one tool
// twenty times shows the repetition instead of flickering on a single line.
func collapseActivity(items []ActivityItem) []ActivityItem {
	var out []ActivityItem
	for _, item := range items {
		if item.Kind == "step" {
			continue
		}
		if n := len(out); n > 0 {
			last := &out[n-1]
			if last.Kind == item.Kind && activityKey(*last) == activityKey(item) {
				last.Repeat++
				last.Done = item.Done
				last.Success = item.Success
				// The newest call's outcome is the one worth showing; an older
				// repeat's `└` line describes work already superseded. The dev
				// payloads follow it for the same reason: showing call #1's
				// arguments under a line reading "x14" would be a lie about
				// which call they came from.
				last.Detail = item.Detail
				last.Args = item.Args
				last.Output = item.Output
				continue
			}
		}
		out = append(out, item)
	}
	return out
}

// activityKey is what "the same activity twice" means: for a tool, the tool
// itself, so "Triaging message m1" and "Triaging message m2" fold together.
// Keyed off the raw tool name rather than the narrated prose — the prose is
// SUPPOSED to differ per call, which is exactly why it cannot be the key. The
// ":" split is the fallback for the legacy transport, whose activity items
// carry no Tool.
func activityKey(item ActivityItem) string {
	if item.Kind != "tool" {
		return item.Content
	}
	if item.Tool != "" {
		return item.Tool
	}
	if i := strings.Index(item.Content, ":"); i >= 0 {
		return item.Content[:i]
	}
	return item.Content
}

func formatElapsed(d time.Duration) string {
	total := int(d.Seconds())
	return fmt.Sprintf("%d:%02d", total/60, total%60)
}

// renderActivityItem renders one work-log entry: the action, then at most one
// indented outcome line under it. Returns the lines so a caller can budget the
// live region's height.
//
// live marks the entry that is happening RIGHT NOW — it gets the spinner and
// the elapsed clock instead of a static marker, so the timer is always attached
// to a description of what it is timing.
//
// Failure is never signalled by colour or a glyph alone: a failed call's outcome
// line begins with the word "failed" (see toolResultDetail / failureDetail), so
// the state survives a terminal with no colour.
func (m ChatModel) renderActivityItem(item ActivityItem, live bool, elapsed time.Duration) []string {
	width := m.logWidth()
	// The counter is reserved BEFORE truncating, not appended after. Appending
	// after meant any narration already at the cap — which shell commands and
	// long paths routinely are, and which are exactly the calls that repeat —
	// had its "x14" cut straight back off.
	content := item.Content
	if item.Repeat > 0 {
		suffix := fmt.Sprintf(" x%d", item.Repeat+1)
		content = truncateRunes(content, width-len(suffix)) + suffix
	} else {
		content = truncateRunes(content, width)
	}

	var head string
	switch {
	case live:
		head = m.renderLiveLine(content, elapsed)
	case item.Kind == "tool" || item.Kind == "confirm":
		style := toolNameStyle
		if item.Success != nil && !*item.Success {
			style = failStyle
		}
		head = "  " + activityStyle.Render(glyphTool) + " " + style.Render(content)
	case item.Kind == "status" || item.Kind == "thinking":
		head = "  " + activityStyle.Render(glyphStatus) + " " +
			lipgloss.NewStyle().Foreground(theme.Warning).Render(content)
	default:
		head = "    " + activityStyle.Render(content)
	}

	lines := []string{head}
	if detail := truncateRunes(clean(item.Detail), width-2); detail != "" {
		style := activityStyle
		if item.Success != nil && !*item.Success {
			style = failStyle
		}
		lines = append(lines, "    "+style.Render(glyphDetail+" "+detail))
	}
	// Developer mode only. Args and Output are populated nowhere else, so the
	// user-mode log is genuinely unchanged rather than merely filtered here.
	for _, extra := range []struct{ label, text string }{
		{"args", item.Args},
		{"out", item.Output},
	} {
		if extra.text == "" {
			continue
		}
		label := extra.label + " "
		if body := truncateRunes(extra.text, width-6-len(label)); body != "" {
			lines = append(lines, "      "+devPayloadStyle.Render(label+body))
		}
	}
	return lines
}

// queuedEchoFloor is the narrowest the echoed line may get before the key hint
// beside it is dropped instead. Showing WHAT was accepted is the row's job; the
// key is the part a user can find elsewhere.
const queuedEchoFloor = 24

// renderQueuedRow shows a follow-up that is waiting for the running turn.
//
// The hint names the whole consequence. Esc here does not just un-queue: it
// runs the same cancel every other Esc runs, so the turn being waited on stops
// too — the row used to promise only the half the reader would like.
//
// It is also measured against the terminal rather than the answer column. The
// echo was truncated to answerWidth (capped at answerMeasure) with the prefix
// and hint appended afterwards, so a long queued line ran past the last column
// and wrapped onto a second row, shearing the status bar below it.
func (m ChatModel) renderQueuedRow() string {
	const (
		prefix = "⏎ queued · "
		hint   = "  Esc stops the turn and puts this back"
	)

	suffix := hint
	budget := m.width - lipgloss.Width(prefix) - lipgloss.Width(hint)
	if budget < queuedEchoFloor {
		suffix = ""
		budget = m.width - lipgloss.Width(prefix)
	}

	return activityStyle.Render(prefix) +
		statusMsgStyle.Render(truncateRunes(m.queued, budget)) +
		activityStyle.Render(suffix)
}

func (m ChatModel) View() string {
	if m.width == 0 {
		return m.renderWelcome()
	}

	header := m.renderHeader()
	divider := dividerStyle.Render(strings.Repeat("─", m.width))
	vpView := m.viewport.View()

	inputView := m.input.View()
	if m.streaming {
		// This row belongs to the user, not to the agent. It once mirrored the
		// live region's action and clock, which put one event on screen twice
		// with two clocks that disagreed — "Thinking about the next step 1:05"
		// three rows above "Thinking about the next step 65s". The live region
		// owns that line; here only the user's own text has anything to add.
		switch {
		case strings.TrimSpace(m.input.Value()) != "":
			inputView = m.input.View() + "  " + activityStyle.Render("⏎ queues")
		case m.queued != "":
			inputView = m.renderQueuedRow()
		default:
			// Mid-turn Enter queues rather than sends, so the idle prompt is
			// untrue while the agent works — an empty composer says it better.
			m.input.Placeholder = ""
			inputView = m.input.View()
		}
	}

	// Built as ranked items and thinned by dropping whole ones, so a narrow
	// terminal loses the wheel hint and keeps the way out — see hints.go.
	hint := fitHints(m.statusHints(), m.hintBudget())

	// Steps is deliberately not passed: the bar renders it only when the hint is
	// empty, which it never is here, and the step count already rides the hint
	// under --dev. Passing it kept a second renderer for one number alive, one
	// that would print it in user mode the day the hint did come back empty.
	statusBar := components.RenderStatusBar(components.StatusBarState{
		AgentName: m.agentName,
		Connected: m.connected,
		Streaming: m.streaming,
		Hint:      hint,
	}, m.width)

	// The bypass banner sits OUTSIDE the viewport, directly under the header,
	// so it is in every frame and cannot be scrolled away. When bypass is off
	// it renders to "" and JoinVertical drops it, costing no row.
	rows := []string{header}
	if banner := m.renderBypassBanner(); banner != "" {
		rows = append(rows, banner)
	}
	rows = append(rows, divider, vpView, divider, inputView, statusBar)
	return lipgloss.JoinVertical(lipgloss.Left, rows...)
}

// extractCommandFromArgs pulls the one argument worth showing out of a legacy
// tool_args payload. Every truncation goes through truncateRunes: the previous
// byte slice at [:60] split multi-byte runes, so a command touching a path with
// any non-ASCII character rendered a replacement glyph mid-word.
func extractCommandFromArgs(raw json.RawMessage) string {
	const argWidth = 60

	var args map[string]interface{}
	if err := json.Unmarshal(raw, &args); err != nil {
		return truncateRunes(clean(string(raw)), argWidth)
	}
	// Look for common command fields
	for _, key := range []string{"command", "cmd", "query", "path", "file"} {
		if v, ok := args[key]; ok {
			return truncateRunes(clean(fmt.Sprintf("%v", v)), argWidth)
		}
	}
	// Fallback: show first value
	for _, v := range args {
		return truncateRunes(clean(fmt.Sprintf("%v", v)), argWidth)
	}
	return ""
}

// renderHeader draws the product name, and the agent's name only when it adds
// something. The flagship agent is itself called GAIA, so the generic form
// rendered "GAIA │ GAIA" — a divider separating a word from itself.
func (m ChatModel) renderHeader() string {
	title := headerStyle.Render("GAIA")
	if !isBrandName(m.agentName) {
		title += lipgloss.NewStyle().Foreground(theme.Text).Render(" │ " + m.agentName)
	}
	// Developer mode is worth stating on every frame: it also redirects the
	// agent's file logging to DEBUG, so someone reading a log full of detail —
	// or an empty one — needs to be able to see which mode produced it.
	if m.dev {
		title += activityStyle.Render(" │ dev")
	}
	// Names the specific model in use (never a bare "claude") and colors it
	// when inference is remote — worth stating on every frame so the user can
	// always tell where inference runs and which model answered.
	title += m.renderModelChip()
	return title
}

// isBrandName reports whether an agent's display name is just the product name,
// so callers can drop the duplicate rather than print it twice.
func isBrandName(agent string) bool {
	return strings.EqualFold(strings.TrimSpace(agent), "gaia")
}
