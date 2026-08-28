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
	mu      sync.RWMutex
	seq     int
	lastRaw string
	frames  []Frame
	cols    int
	rows    int
	snap    Snapshot
	started time.Time

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

	if raw == s.lastRaw && s.seq > 0 {
		if markAdvanced {
			s.broadcast()
		}
		return
	}
	s.seq++
	s.lastRaw = raw
	s.frames = append(s.frames, Frame{
		Seq:    s.seq,
		AtMS:   time.Since(s.started).Milliseconds(),
		Screen: PlainScreen(raw),
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
		r.state.debugf("inject: key %q reached the model", m.String())
	}

	next, cmd := r.inner.Update(msg)
	r.inner = next
	r.state.setSnapshot(snapshotOf(next))
	return r, cmd
}

func (r Recorder) View() string {
	view := r.inner.View()
	r.state.recordFrame(view)
	return view
}

func snapshotOf(m tea.Model) Snapshot {
	if sp, ok := m.(SnapshotProvider); ok {
		return sp.ControlSnapshot()
	}
	return Snapshot{View: ViewUnknown}
}
