package control

import (
	"strings"
	"sync"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// maxFrames caps the rendered-frame history kept for GET /frames.
const maxFrames = 200

// Snapshot is the navigation state the control API reports. A model that can
// describe itself implements [SnapshotProvider]; anything else is reported with
// View "unknown" rather than a guess.
type Snapshot struct {
	View      string `json:"view"`
	Agent     string `json:"agent"`
	Streaming bool   `json:"streaming"`
	Overlay   string `json:"overlay,omitempty"`

	// Blocker is the key of the readiness row refusing the launch, empty when
	// none is. It exists so a client can assert WHY the gate is holding from
	// model state instead of grepping the rendered remedy for a phrase — the
	// screen's wording is allowed to change; the row key is not.
	Blocker string `json:"blocker,omitempty"`

	// Chat is the conversation view's own diagnostics, nil everywhere else.
	Chat *ChatState `json:"chat,omitempty"`
}

// ChatState is what a driver needs to tell a real scroll or mouse defect from
// a mis-aimed test.
//
// Every field here answers a question that the rendered screen cannot: whether
// the transcript is pinned to the newest content or parked where the reader
// left it, whether the app or the terminal currently owns the mouse, and how
// many rows of content exist above the window. Without them, "I scrolled and
// nothing moved" is indistinguishable from "I was already at the top", and
// "my click did nothing" from "the app never had the mouse".
type ChatState struct {
	Messages int `json:"messages"`
	// ScrollY is the first content row visible in the window, and ContentRows
	// the total the transcript has laid out.
	ScrollY     int  `json:"scroll_y"`
	ContentRows int  `json:"content_rows"`
	AtBottom    bool `json:"at_bottom"`
	// FollowTail is whether streamed output still drags the view down. It goes
	// false the moment the reader scrolls away and true again at the bottom,
	// so it is the field that says whether a scroll was registered at all.
	FollowTail bool `json:"follow_tail"`
	// MouseOwner is "app" while the TUI is tracking the mouse (the wheel
	// scrolls, links are clickable) or "terminal" while it is not — either
	// because the user asked for SELECT MODE or because nothing wants it yet.
	MouseOwner string `json:"mouse_owner"`
	// MouseMotion is "cell" or "all" — which tracking mode is armed. An
	// overlay needs "all" for hover; the plain transcript does not.
	MouseMotion string `json:"mouse_motion,omitempty"`
	// SelectMode is the user's own Ctrl+T choice, independent of who happens
	// to hold the mouse this frame.
	SelectMode bool `json:"select_mode"`
	// ViewportRows is the height of the transcript window in rows, and
	// HeaderRows how many screen rows sit above it — the offset a client adds
	// to a content row to get a screen row to click.
	ViewportRows int  `json:"viewport_rows"`
	HeaderRows   int  `json:"header_rows"`
	HelpOpen     bool `json:"help_open"`

	// Cost is what the session has spent so far, nil before the first turn.
	Cost *SessionCost `json:"cost,omitempty"`
}

// SessionCost is the session ledger the TUI keeps: what a driver needs to
// answer "what did that task cost" without re-deriving it from a transcript.
//
// Every field is summed from what the inference backend reported. A turn whose
// backend reported no token counts is counted in Turns but contributes nothing
// to the token totals, and MeasuredTurns says how many were measured — so a
// caller can tell a genuinely cheap session from a partly-unmeasured one.
type SessionCost struct {
	Turns         int     `json:"turns"`
	MeasuredTurns int     `json:"measured_turns"`
	WallSeconds   float64 `json:"wall_seconds"`
	Steps         int     `json:"steps"`
	ToolCalls     int     `json:"tool_calls"`
	InputTokens   int     `json:"input_tokens"`
	OutputTokens  int     `json:"output_tokens"`
	CachedTokens  int     `json:"cached_tokens"`
	Model         string  `json:"model,omitempty"`
	// USD is nil when no price is configured for this model — a cost readout
	// must not invent a rate.
	USD *float64 `json:"usd,omitempty"`
}

// Every view the TUI can report. A client waits on one of these, so they are
// constants rather than literals scattered through the view packages.
const (
	// ViewUnknown is reported when the running model cannot describe its own
	// state.
	ViewUnknown = "unknown"
	// ViewSplash is the mascot frame the launch opens on.
	ViewSplash = "splash"
	// ViewPreflight is the readiness gate.
	ViewPreflight = "preflight"
	// ViewChat is the conversation.
	ViewChat = "chat"
)

// SnapshotProvider is implemented by a root model that can report where the
// user currently is. Keeping it an interface means the control package never
// imports the view packages, so the two can evolve independently.
type SnapshotProvider interface {
	ControlSnapshot() Snapshot
}

// Frame is one rendered screen, kept for debugging what happened.
type Frame struct {
	Seq    int    `json:"seq"`
	AtMS   int64  `json:"at_ms"`
	Screen string `json:"screen"`
	// Raw is the same frame with its styling intact. Screen stays stripped —
	// it is what a caller greps — but a picture built from stripped text is a
	// grey wash that looks nothing like the terminal, so the colours have to
	// survive the ring too.
	Raw string `json:"raw,omitempty"`
}

// MarkMsg is a sentinel the control server injects after a batch of keys.
//
// Bubble Tea processes messages in order and renders after each one, so once a
// frame has been drawn *for the mark*, every key sent before it has been both
// handled and rendered. Program.Send only queues, so without this a caller that
// sends three keys and reads the screen can see a mid-sequence frame.
type MarkMsg struct{ ID int64 }

// State holds everything the HTTP handlers read: the last rendered frame, the
// frame history, the terminal size, and the model's own snapshot.
//
// Bubble Tea calls Update and View from its event loop; HTTP handlers read from
// their own goroutines. Every field is guarded by mu.
type State struct {
	mu        sync.RWMutex
	seq       int
	lastRaw   string
	lastPlain string
	frames    []Frame
	cols      int
	rows      int
	snap      Snapshot
	started   time.Time

	// pendingMark is set when Update sees a MarkMsg; renderedMark is promoted
	// from it by the View that immediately follows.
	pendingMark  int64
	renderedMark int64
	markCounter  int64

	// changed is closed (and replaced) whenever the frame or the snapshot
	// changes, so POST /wait blocks instead of busy-polling.
	changed chan struct{}

	debugf func(format string, args ...any)
}

// NewState creates the shared state. debugf may be nil.
func NewState(debugf func(format string, args ...any)) *State {
	if debugf == nil {
		debugf = func(string, ...any) {}
	}
	return &State{
		snap:    Snapshot{View: ViewUnknown},
		started: time.Now(),
		changed: make(chan struct{}),
		debugf:  debugf,
	}
}

// Changed returns the channel that closes on the next frame or state change.
// Callers must take the channel BEFORE re-checking the condition, or they can
// miss a wakeup that lands between the check and the wait.
func (s *State) Changed() <-chan struct{} {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.changed
}

// broadcast wakes every waiter. Caller must hold the write lock.
func (s *State) broadcast() {
	close(s.changed)
	s.changed = make(chan struct{})
}

// recordFrame caches a newly rendered frame and promotes the pending mark.
// Identical consecutive frames are dropped from the history — Bubble Tea
// re-renders on every message, including spinner ticks — but the mark is
// promoted either way, because a key that changes nothing visible is still
// handled.
func (s *State) recordFrame(raw string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	markAdvanced := s.renderedMark != s.pendingMark
	s.renderedMark = s.pendingMark

	// Deduped on the VISIBLE screen, not the styled bytes. The composer's
	// cursor blinks, which changes the bytes a couple of times a second while
	// the screen says exactly the same thing — and keying the ring on that
	// filled all 200 slots with one motionless frame, pushing the history
	// anyone would want to watch out the back.
	plain := PlainScreen(raw)
	if plain == s.lastPlain && s.seq > 0 {
		s.lastRaw = raw
		if markAdvanced {
			s.broadcast()
		}
		return
	}
	s.seq++
	s.lastRaw = raw
	s.lastPlain = plain
	s.frames = append(s.frames, Frame{
		Seq:    s.seq,
		AtMS:   time.Since(s.started).Milliseconds(),
		Screen: plain,
		Raw:    raw,
	})
	if len(s.frames) > maxFrames {
		s.frames = s.frames[len(s.frames)-maxFrames:]
	}
	s.broadcast()
}

// NextMark reserves a sentinel id for the next injected batch.
func (s *State) NextMark() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.markCounter++
	return s.markCounter
}

// setPendingMark records that a MarkMsg reached the model.
//
// Monotonic on purpose: settle waits for "rendered >= my mark", so a mark
// going backwards would let an earlier waiter return before its own keys were
// processed. Injection is serialized, but this keeps the invariant local.
func (s *State) setPendingMark(id int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if id > s.pendingMark {
		s.pendingMark = id
	}
}

// RenderedMark is the newest mark whose frame has been drawn and cached.
func (s *State) RenderedMark() int64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.renderedMark
}

// setSnapshot stores the model's self-reported state and logs transitions.
func (s *State) setSnapshot(snap Snapshot) {
	s.mu.Lock()
	defer s.mu.Unlock()
	prev := s.snap
	if snapshotsEqual(prev, snap) {
		return
	}
	s.snap = snap
	s.broadcast()
	if prev.View != snap.View || prev.Agent != snap.Agent || prev.Streaming != snap.Streaming {
		s.debugf("state: view %s→%s agent %q→%q streaming %v→%v",
			prev.View, snap.View, prev.Agent, snap.Agent, prev.Streaming, snap.Streaming)
	}
}

// SetSize records the terminal size the model is laid out for.
func (s *State) SetSize(cols, rows int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cols == cols && s.rows == rows {
		return
	}
	s.cols, s.rows = cols, rows
	s.broadcast()
	s.debugf("state: size %dx%d", cols, rows)
}

// Size returns the last known terminal size.
func (s *State) Size() (cols, rows int) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.cols, s.rows
}

// Current returns the last rendered frame (raw, ANSI intact), its sequence
// number, and the model snapshot — read atomically so a caller never mixes a
// frame with a snapshot from a different render.
func (s *State) Current() (raw string, seq int, snap Snapshot) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.lastRaw, s.seq, s.snap
}

// Frames returns frames with Seq > since, newest last, capped at limit.
// A limit <= 0 means no cap — the ring itself bounds the result at maxFrames.
// truncated reports whether older frames were dropped from the ring.
func (s *State) Frames(since, limit int) (frames []Frame, latestSeq int, truncated bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, f := range s.frames {
		if f.Seq > since {
			frames = append(frames, f)
		}
	}
	if len(s.frames) > 0 && s.frames[0].Seq > since+1 {
		truncated = true
	}
	if limit > 0 && len(frames) > limit {
		frames = frames[len(frames)-limit:]
		truncated = true
	}
	return frames, s.seq, truncated
}

// UptimeMS is how long the state has been collecting frames.
func (s *State) UptimeMS() int64 {
	return time.Since(s.started).Milliseconds()
}

func snapshotsEqual(a, b Snapshot) bool { return a == b }

// PlainScreen strips ANSI styling and trailing padding so the result is what an
// assistant should read. Lipgloss pads every line to the layout width; keeping
// that padding just wastes tokens and makes diffs unreadable.
func PlainScreen(raw string) string {
	stripped := ansi.Strip(raw)
	lines := strings.Split(stripped, "\n")
	for i, line := range lines {
		lines[i] = strings.TrimRight(line, " \t\r")
	}
	for len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return strings.Join(lines, "\n")
}

// Recorder wraps the root model so every rendered frame is cached for the
// control API. Bubble Tea does not expose the last frame it drew, and scraping
// the terminal is not an option, so the model itself reports it.
//
// Recorder is a value type (like every Bubble Tea model) but carries a pointer
// to the shared State, so mutations survive the copies Bubble Tea makes.
type Recorder struct {
	inner tea.Model
	state *State
}

// NewRecorder wraps inner, publishing its frames and snapshots into state.
//
// The snapshot is seeded here rather than at Init so /status answers with the
// real view from the moment the server is up, not "unknown" until the first
// message arrives.
func NewRecorder(inner tea.Model, state *State) Recorder {
	state.setSnapshot(snapshotOf(inner))
	return Recorder{inner: inner, state: state}
}

// State returns the shared state the control server serves from.
func (r Recorder) State() *State { return r.state }

func (r Recorder) Init() tea.Cmd {
	r.state.setSnapshot(snapshotOf(r.inner))
	return r.inner.Init()
}

func (r Recorder) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch m := msg.(type) {
	case MarkMsg:
		// The sentinel is ours; the wrapped model must never see it.
		r.state.setPendingMark(m.ID)
		return r, nil
	case tea.WindowSizeMsg:
		r.state.SetSize(m.Width, m.Height)
	case tea.KeyMsg:
		r.state.debugf("inject: key reached the model (content omitted)")
	}

	next, cmd := r.inner.Update(msg)
	r.inner = next
	return r, cmd
}

// View publishes the snapshot and the frame TOGETHER.
//
// The snapshot used to be taken in Update, which runs before Bubble Tea draws.
// A client that waited on state and then read the screen therefore got the
// frame from BEFORE the change it had just waited for — POST /wait would
// return on `blocker: "binary"` and GET /screen would still say "checking…".
// Current() documents these as read atomically; publishing them a render apart
// made that untrue at exactly the moment a caller cares.
func (r Recorder) View() string {
	view := r.inner.View()
	r.state.setSnapshot(snapshotOf(r.inner))
	r.state.recordFrame(view)
	return view
}

func snapshotOf(m tea.Model) Snapshot {
	if sp, ok := m.(SnapshotProvider); ok {
		return sp.ControlSnapshot()
	}
	return Snapshot{View: ViewUnknown}
}
