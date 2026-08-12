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
type errMsg struct{ err error }
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

	userStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(theme.Info)

	assistantStyle = lipgloss.NewStyle().
			Foreground(theme.Text)

	errorStyle = lipgloss.NewStyle().
			Foreground(theme.Danger)

	activityStyle = lipgloss.NewStyle().
			Foreground(theme.Dim)

	toolNameStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(theme.Info)

	successStyle = lipgloss.NewStyle().
			Foreground(theme.Success)

	failStyle = lipgloss.NewStyle().
			Foreground(theme.Danger)

	dividerStyle = lipgloss.NewStyle().
			Foreground(theme.Divider)

	thinkingStyle = lipgloss.NewStyle().
			Foreground(theme.Success)

	stepStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(theme.Info)

	statusMsgStyle = lipgloss.NewStyle().
			Foreground(theme.Dim).
			Italic(true)

	answerPanelStyle = lipgloss.NewStyle().
				Border(lipgloss.RoundedBorder()).
				BorderForeground(theme.Success).
				Padding(0, 1)

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
	// buffer accumulates streamed answer text. A plain string, not a
	// strings.Builder: Bubble Tea copies the model on every update, and a
	// Builder panics the moment a copied non-zero one is written to again.
	buffer string

	input    textarea.Model
	viewport viewport.Model
	spinner  spinner.Model

	client    client.AgentClient
	events    <-chan interface{}
	cancelFn  context.CancelFunc
	agentName string
	agentID   string
	debug     bool
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

	connected    bool
	totalSteps   int
	initialQuery string
	err          error
	queryStart   time.Time // tracks when the current query started
	firstToken   bool      // whether the first real inference token has arrived this turn (not just any SSE frame)
	ttft         time.Duration

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

func NewChatModel(c client.AgentClient, agentName string, initialQuery string, debug bool) ChatModel {
	ti := textarea.New()
	ti.Placeholder = "Ask anything... (Enter to send, Ctrl+C to quit)"
	ti.Focus()
	ti.CharLimit = 4096
	ti.SetHeight(1)
	ti.ShowLineNumbers = false

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(theme.Highlight)

	vp := viewport.New(80, 20)
	vp.SetContent("")

	return ChatModel{
		client:       c,
		agentName:    agentName,
		agentID:      agentName,
		initialQuery: initialQuery,
		debug:        debug,
		input:        ti,
		spinner:      sp,
		viewport:     vp,
		connected:    true,
	}
}

// NewChatModelFromHub creates a ChatModel launched from the hub, enabling Esc-to-return behavior.
func NewChatModelFromHub(c client.AgentClient, agentID, agentName string, debug bool) ChatModel {
	m := NewChatModel(c, agentName, "", debug)
	m.agentID = agentID
	m.fromHub = true
	return m
}

// NewChatModelForCatalogAgent creates a standalone ChatModel (esc quits -- see
// CanReturnToHub) for a real catalog agent, so agentID is the catalog id
// rather than NewChatModel's default of the display name.
func NewChatModelForCatalogAgent(c client.AgentClient, agentID, agentName string, debug bool) ChatModel {
	m := NewChatModel(c, agentName, "", debug)
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
	} else if m.debug && m.preScanGateMismatch() {
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

func (m ChatModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
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
		m.events = nil
		m.cancelFn = nil
		m.question = nil
		m.confirmation = nil
		m.flushBuffer()
		m.activity = nil
		// The channel closing is the settlement signal a cancel was waiting
		// on (#2901) — the "cancelling…" line posted at request time becomes
		// "cancelled" only now, once it is actually confirmed rather than
		// merely requested.
		if m.cancelPending {
			m.cancelPending = false
			m.messages = append(m.messages, Message{
				Role:    RoleStatus,
				Content: "cancelled",
			})
		}
		m.updateViewport()
		return m, nil

	case errMsg:
		m.streaming = false
		m.events = nil
		m.cancelFn = nil
		// A pending cancel settles here too — the run ended in an error
		// instead of the clean close doneMsg handles, but either way the
		// composer must not stay hostage to a cancel that will never see its
		// happy-path settlement message (the guards in handleKey key off
		// this alongside m.streaming).
		m.cancelPending = false
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

	if !m.streaming {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		cmds = append(cmds, cmd)
	}

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
		return m, tea.Quit

	case tea.KeyEsc:
		if m.streaming && m.cancelFn != nil && !m.cancelPending {
			return m.requestCancel()
		}
		if m.fromHub {
			return m, func() tea.Msg {
				return ReturnToHubMsg{AgentID: m.agentID}
			}
		}
		return m, tea.Quit

	case tea.KeyEnter:
		if m.streaming {
			return m, nil
		}
		if msg.Alt {
			return m, nil
		}
		query := strings.TrimSpace(m.input.Value())
		if query == "" {
			return m, nil
		}
		m.input.Reset()

		// Handle slash commands
		switch {
		case query == "/help":
			return m, func() tea.Msg { return ToggleHelpMsg{} }
		case query == "/hub":
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
		case query == "/clear":
			m.messages = nil
			// Daemon-transport agents are stateless per turn: the host pushes the
			// transcript back as `context`, so clearing the view must clear that
			// too or the "cleared" history keeps being sent.
			if r, ok := m.client.(client.TranscriptResetter); ok {
				r.ResetTranscript()
			}
			m.updateViewport()
			return m, nil
		}

		return m.sendQuery(query)

	case tea.KeyPgUp:
		m.viewport.HalfViewUp()
		return m, nil

	case tea.KeyPgDown:
		m.viewport.HalfViewDown()
		return m, nil
	}

	if !m.streaming {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}

	return m, nil
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
// m.events untouched — the existing read keeps running, and the channel only
// closes when the server's own stream generator does, which is proven (see
// SSEClient.Cancel's doc comment) to happen strictly after run_lock.release().
// That real close is what the doneMsg case in Update waits on before flipping
// m.streaming back off and re-enabling Enter.
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
	m.messages = append(m.messages, Message{
		Role:    RoleStatus,
		Content: "cancelling…",
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

func (m ChatModel) sendQuery(query string) (tea.Model, tea.Cmd) {
	m.messages = append(m.messages, Message{
		Role:    RoleUser,
		Content: query,
	})
	m.streaming = true
	m.activity = nil
	m.buffer = ""
	m.queryStart = time.Now()
	m.firstToken = false
	m.ttft = 0
	// A new turn starts having drawn no card yet -- see drainPendingPreScan.
	m.preScanRenderedThisTurn = false
	m.updateViewport()

	ctx, cancel := context.WithCancel(context.Background())
	m.cancelFn = cancel

	c := m.client
	return m, tea.Batch(
		m.spinner.Tick,
		func() tea.Msg {
			ch, err := c.Send(ctx, query)
			if err != nil {
				return errMsg{err: err}
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
		// Truncate long summaries (stdout can be very long)
		if len(summary) > 60 {
			summary = summary[:60] + "..."
		}
		// Clean up newlines in summary
		summary = strings.ReplaceAll(summary, "\n", " ")
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
		m.updateViewport()
		return m, nil

	case event.ErrorEvent:
		m.messages = append(m.messages, Message{
			Role:    RoleError,
			Content: sanitizeErrorText(e.Content),
		})
		m.streaming = false
		m.activity = nil
		m.updateViewport()
		return m, nil

	case event.DoneEvent:
		m.flushBuffer()
		m.streaming = false
		m.activity = nil
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

func (m *ChatModel) resize() {
	headerH := 1
	statusH := 1
	inputH := 3
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

	components.SetWordWrap(vpWidth - 4)
	if m.question != nil {
		m.question.SetWidth(m.cardWidth())
	}
	if m.confirmation != nil {
		m.confirmation.SetWidth(m.cardWidth())
	}
	m.updateViewport()
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
		}
		// By index, not by value: rendering a card memoizes onto the message.
		sb.WriteString(m.renderMessage(&m.messages[i], seen))
		sb.WriteString("\n")
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

	buf := m.buffer
	if m.streaming && buf != "" {
		sb.WriteString(assistantStyle.Render(buf))
		sb.WriteString("\n")
	}

	m.viewport.SetContent(sb.String())
	m.viewport.GotoBottom()
}

func (m ChatModel) renderWelcome() string {
	title := lipgloss.NewStyle().
		Bold(true).
		Foreground(theme.AccentBright).
		Render("Welcome to GAIA")

	agent := lipgloss.NewStyle().
		Foreground(theme.Text).
		Render("Connected to: " + m.agentName)

	hint := activityStyle.Render("Type a message and press Enter to start chatting.\nType /help for available commands.")

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

// wrapForPane wraps text to the visible pane, leaving it untouched before the
// first WindowSizeMsg (when no width is known yet).
func (m ChatModel) wrapForPane(s string) string {
	if m.width <= 0 {
		return s
	}
	return components.WrapText(s, m.cardWidth())
}

// renderMessage draws one message. seen threads cross-card dedup for the
// RoleCard case (see Message.renderCardDeduped); pass nil for a standalone
// render with no dedup.
func (m ChatModel) renderMessage(msg *Message, seen map[string]bool) string {
	switch msg.Role {
	case RoleUser:
		// A free-text answer to a mid-run question lands here and can be long.
		return m.wrapForPane(userStyle.Render("▶ You: ") + msg.Content)

	case RoleAssistant:
		content := msg.Content
		if msg.Rendered != "" {
			content = msg.Rendered
		}
		panelWidth := m.width - 4
		if panelWidth < 20 {
			panelWidth = 20
		}
		panel := answerPanelStyle.Width(panelWidth).Render(content)

		// Perf stats line below the panel
		if msg.Duration > 0 {
			var stats []string
			stats = append(stats, fmt.Sprintf("%.1fs", msg.Duration.Seconds()))
			if msg.TTFT > 0 {
				stats = append(stats, fmt.Sprintf("ttft %.1fs", msg.TTFT.Seconds()))
			}
			// Real generated-token count (#2899) — no longer a char-count
			// guess. Zero means the sidecar didn't report usage.tokens (e.g.
			// the legacy transport, or an older agent); omit rather than
			// fall back to the old estimate, which would silently
			// reintroduce the exact bug this replaced.
			if msg.Tokens > 0 {
				stats = append(stats, fmt.Sprintf("%d tokens", msg.Tokens))
				// Tokens per second (output only)
				inferTime := msg.Duration - msg.TTFT
				if inferTime > 0 {
					tps := float64(msg.Tokens) / inferTime.Seconds()
					stats = append(stats, fmt.Sprintf("%.1f tok/s", tps))
				}
			}
			if msg.Steps > 0 {
				stats = append(stats, fmt.Sprintf("%d steps", msg.Steps))
			}
			if msg.ToolsUsed > 0 {
				stats = append(stats, fmt.Sprintf("%d tools", msg.ToolsUsed))
			}
			statsLine := activityStyle.Render("  " + strings.Join(stats, " · "))
			panel += "\n" + statsLine
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

// workLogLines caps the live work log. Bounded so a long turn cannot push the
// transcript off screen, deep enough that repeated tool calls read as progress.
const workLogLines = 5

// stillWorkingAfter is when the live region starts saying the wait is expected.
// A local 4B model routinely takes 60-90s on an inbox triage; without this line
// the user's next move is ctrl+c.
const stillWorkingAfter = 20 * time.Second

// renderLiveRegion draws a bounded work log for the running turn: a header with
// the current step and elapsed time, then the last few activity lines with
// consecutive repeats folded into a counter.
//
// Bounded, not two lines: on a turn touching dozens of messages, two static
// lines are indistinguishable from a hang.
func (m ChatModel) renderLiveRegion() string {
	var lines []string

	elapsed := time.Since(m.queryStart)
	header := "Working"
	for i := len(m.activity) - 1; i >= 0; i-- {
		if m.activity[i].Kind == "step" {
			header = m.activity[i].Content
			break
		}
	}
	lines = append(lines, "  "+stepStyle.Render(m.spinner.View()+" "+header)+"  "+
		activityStyle.Render(formatElapsed(elapsed)))

	log := collapseActivity(m.activity)
	if len(log) > workLogLines {
		log = log[len(log)-workLogLines:]
	}
	for _, item := range log {
		lines = append(lines, m.renderActivityItem(item))
	}
	if len(log) == 0 {
		lines = append(lines, "  "+activityStyle.Render("connecting..."))
	}

	if elapsed >= stillWorkingAfter {
		lines = append(lines, "  "+activityStyle.Render("└ still working — local model, usually 60-90s"))
	}

	return strings.Join(lines, "\n")
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
				continue
			}
		}
		out = append(out, item)
	}
	return out
}

// activityKey is what "the same activity twice" means: for a tool, the tool name
// without its arguments, so `send_email: a@x` and `send_email: b@y` fold together.
func activityKey(item ActivityItem) string {
	if item.Kind != "tool" {
		return item.Content
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

// renderActivityItem renders a single work-log line. Markers are ASCII words and
// punctuation, never emoji or colour alone — the state has to survive a terminal
// with no colour and no emoji font.
func (m ChatModel) renderActivityItem(item ActivityItem) string {
	content := item.Content
	if item.Repeat > 0 {
		content += fmt.Sprintf(" x%d", item.Repeat+1)
	}

	switch item.Kind {
	case "thinking":
		if len(content) > 72 {
			content = content[:72] + "..."
		}
		return "       " + thinkingStyle.Render(content)

	case "tool":
		if item.Done {
			if item.Success != nil && !*item.Success {
				return "  " + failStyle.Render("[x] ") + toolNameStyle.Render(content)
			}
			return "  " + successStyle.Render("[ok] ") + toolNameStyle.Render(content)
		}
		return "  " + activityStyle.Render("[..] ") + toolNameStyle.Render(content)

	case "confirm":
		// Always added already-resolved (see resolveConfirmationDecision) — a
		// confirmation has no separate "in progress" activity line, since the
		// modal itself is the in-progress view.
		if item.Success != nil && !*item.Success {
			return "  " + failStyle.Render("[x] ") + toolNameStyle.Render(content)
		}
		return "  " + successStyle.Render("[ok] ") + toolNameStyle.Render(content)

	case "status":
		return "       " + lipgloss.NewStyle().Foreground(theme.Warning).Render(content)

	default:
		return "       " + activityStyle.Render(content)
	}
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
		elapsed := time.Since(m.queryStart)
		elapsedStr := fmt.Sprintf("%.0fs", elapsed.Seconds())

		label := "Waiting for agent..."
		if len(m.activity) > 0 {
			last := m.activity[len(m.activity)-1]
			switch last.Kind {
			case "tool":
				parts := strings.SplitN(last.Content, ":", 2)
				label = "Using " + parts[0] + "..."
			case "thinking":
				label = "Thinking..."
			case "step":
				label = last.Content
			case "status":
				label = last.Content
			}
		}
		inputView = m.spinner.View() + " ◆ " + label + "  " + activityStyle.Render(elapsedStr)
	}

	hint := "Ctrl+C quit"
	if m.streaming {
		hint = "Esc cancel"
	} else if m.fromHub {
		hint = "Esc back · Ctrl+C quit"
	}

	statusBar := components.RenderStatusBar(components.StatusBarState{
		AgentName: m.agentName,
		Connected: m.connected,
		Steps:     m.totalSteps,
		Streaming: m.streaming,
		Hint:      hint,
	}, m.width)

	return lipgloss.JoinVertical(lipgloss.Left,
		header,
		divider,
		vpView,
		divider,
		inputView,
		statusBar,
	)
}

// extractCommandFromArgs tries to extract a clean command string from tool args JSON.
func extractCommandFromArgs(raw json.RawMessage) string {
	var args map[string]interface{}
	if err := json.Unmarshal(raw, &args); err != nil {
		s := string(raw)
		if len(s) > 60 {
			s = s[:60] + "..."
		}
		return s
	}
	// Look for common command fields
	for _, key := range []string{"command", "cmd", "query", "path", "file"} {
		if v, ok := args[key]; ok {
			s := fmt.Sprintf("%v", v)
			if len(s) > 60 {
				s = s[:60] + "..."
			}
			return s
		}
	}
	// Fallback: show first value
	for _, v := range args {
		s := fmt.Sprintf("%v", v)
		if len(s) > 60 {
			s = s[:60] + "..."
		}
		return s
	}
	return ""
}

func (m ChatModel) renderHeader() string {
	title := headerStyle.Render("GAIA")
	name := lipgloss.NewStyle().Foreground(theme.Text).Render(" │ " + m.agentName)
	return title + name
}
