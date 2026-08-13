package control

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"golang.org/x/term"

	"github.com/amd/gaia/tui/internal/daemon"
)

// attachedToTerminal reports whether stdout is a real terminal, i.e. whether
// there is a physical viewport a resize could overflow. Swappable for tests.
var attachedToTerminal = func() bool {
	return term.IsTerminal(int(os.Stdout.Fd()))
}

// Sender is the subset of *tea.Program the control server needs. Program.Send
// is safe to call from another goroutine — that is the whole injection
// mechanism — and narrowing it to an interface keeps the server testable
// without booting a terminal.
type Sender interface {
	Send(tea.Msg)
}

// Options configures the control server.
type Options struct {
	// Port to bind. 0 auto-assigns. 4001 is rejected — it is reserved.
	Port int
	// Debug mirrors the TUI's --debug flag: every injected key, every state
	// transition, and every wait resolution is logged to stderr.
	Debug bool
	// Version is reported by /status and written to the discovery file.
	Version string
}

// Server is the running control API.
type Server struct {
	http     *http.Server
	listener net.Listener
	state    *State
	sender   Sender
	token    string
	port     int
	pid      int
	version  string
	debugf   func(format string, args ...any)
	// done is closed by Stop so parked /wait handlers return immediately
	// instead of holding the shutdown open for their full timeout.
	done     chan struct{}
	stopOnce sync.Once

	// discoveryPath is resolved at Start so the caller never has to re-resolve
	// (and handle a second error) between Start and Run.
	discoveryPath string

	// running reports whether the Bubble Tea event loop is consuming messages.
	// tea.Program.Send DISCARDS messages once the program's context is done, so
	// without this a request to a quit TUI would answer 200 for keys that went
	// nowhere.
	running atomic.Bool

	// injectMu serializes injection so one caller's batch is applied as a unit
	// and marks stay ordered. Two concurrent /keys calls would otherwise
	// interleave, and the later mark could satisfy the earlier waiter.
	injectMu sync.Mutex
}

// renderSettleTimeout bounds how long a mutating endpoint waits for the model
// to re-render before answering. It exists so "send keys, then read the screen"
// does not race the render; it is not a retry loop.
const renderSettleTimeout = 250 * time.Millisecond

// defaultWaitTimeout applies when POST /wait omits timeout_ms.
const defaultWaitTimeout = 30 * time.Second

// maxWaitTimeout caps POST /wait so a caller cannot pin a connection forever.
const maxWaitTimeout = 10 * time.Minute

// Debugf returns a logger honouring opts.Debug, for the caller to share with
// NewState so the recorder and the server log through the same switch.
func Debugf(debug bool) func(format string, args ...any) {
	if !debug {
		return func(string, ...any) {}
	}
	return func(format string, args ...any) {
		logf("[control] "+format, args...)
	}
}

// Start binds the control API on loopback and registers it for discovery.
//
// The returned Server must be stopped with Stop so the discovery file does not
// outlive the TUI.
func Start(sender Sender, state *State, opts Options) (*Server, error) {
	if sender == nil {
		return nil, fmt.Errorf("control: no tea.Program to drive; the control server must be started after the program is created")
	}
	if state == nil {
		return nil, fmt.Errorf("control: no shared state; wrap the root model with control.NewRecorder first")
	}
	if opts.Port == ReservedPort {
		return nil, fmt.Errorf("control: port %d is reserved and must never be bound; pick another --control-port or drop the flag to auto-assign", ReservedPort)
	}

	listener, err := listen(opts.Port)
	if err != nil {
		return nil, err
	}
	port := listener.Addr().(*net.TCPAddr).Port

	token, err := newToken()
	if err != nil {
		listener.Close()
		return nil, err
	}

	debugf := Debugf(opts.Debug)
	version := opts.Version
	if version == "" {
		version = "dev"
	}

	s := &Server{
		listener: listener,
		state:    state,
		sender:   sender,
		token:    token,
		port:     port,
		pid:      os.Getpid(),
		version:  version,
		debugf:   debugf,
		done:     make(chan struct{}),
	}

	mux := http.NewServeMux()
	mux.HandleFunc(APIPrefix+"/status", s.auth(s.handleStatus))
	mux.HandleFunc(APIPrefix+"/screen", s.auth(s.handleScreen))
	mux.HandleFunc(APIPrefix+"/keys", s.auth(s.handleKeys))
	mux.HandleFunc(APIPrefix+"/text", s.auth(s.handleText))
	mux.HandleFunc(APIPrefix+"/wait", s.auth(s.handleWait))
	mux.HandleFunc(APIPrefix+"/frames", s.auth(s.handleFrames))
	mux.HandleFunc(APIPrefix+"/resize", s.auth(s.handleResize))
	// Unknown paths answer in the same error envelope as everything else, so a
	// typo'd endpoint is a readable message rather than Go's HTML 404 page.
	mux.HandleFunc("/", s.auth(s.handleNotFound))

	s.http = &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	// Resolved before anything is published, so a failure here cannot leave a
	// discovery file pointing at a server we then tear down.
	path, err := FilePath()
	if err != nil {
		listener.Close()
		return nil, err
	}
	s.discoveryPath = path

	info := Info{
		PID:        s.pid,
		Port:       port,
		Token:      token,
		Host:       Host,
		Service:    ServiceID,
		APIVersion: APIVersion,
		StartedAt:  float64(time.Now().UnixNano()) / 1e9,
		Version:    version,
	}
	// Whoever registers last owns the file. Say so when that displaces a live
	// registration: the older TUI keeps running but becomes undiscoverable.
	if prev, rerr := ReadInfo(); rerr == nil && prev != nil && prev.PID != s.pid && daemon.PIDAlive(prev.PID) {
		takeover := fmt.Sprintf(
			"control: taking over %s, which was registered to pid %d and that pid is still "+
				"in use. If it is another TUI it can no longer be found by a client — quit it, "+
				"or run only one TUI with --control at a time.", path, prev.PID)
		// Printed before p.Run() takes the alt screen, so it is safe on stderr —
		// but the alt screen then wipes it, and this is the one warning that
		// explains why a driver is talking to the wrong session. Mirror it into
		// the log so it outlives the frame that erases it.
		fmt.Fprintln(os.Stderr, takeover)
		logf("%s", takeover)
	}
	if err := WriteInfo(info); err != nil {
		listener.Close()
		return nil, fmt.Errorf("control: cannot publish the discovery file: %w", err)
	}

	go func() {
		err := s.http.Serve(listener)
		if err != nil && err != http.ErrServerClosed {
			// The TUI keeps running, but nothing can drive it any more — say so
			// rather than leaving the caller waiting on a dead socket.
			logf("control API stopped serving: %v — restart the TUI to re-enable it", err)
		}
	}()

	s.debugf("listening on %s:%d (discovery file %s, token withheld)", Host, port, path)
	return s, nil
}

// DiscoveryPath is where this server published its control.json.
func (s *Server) DiscoveryPath() string { return s.discoveryPath }

// MarkRunning must be called immediately before the Bubble Tea program starts
// consuming messages, and MarkStopped once Run returns. Injection endpoints
// refuse while the loop is not running rather than reporting a success for
// messages tea.Program silently discards.
func (s *Server) MarkRunning() { s.running.Store(true) }
func (s *Server) MarkStopped() { s.running.Store(false) }

// listen binds loopback, never the reserved port.
func listen(port int) (net.Listener, error) {
	if port != 0 {
		l, err := net.Listen("tcp", fmt.Sprintf("%s:%d", Host, port))
		if err != nil {
			return nil, fmt.Errorf("control: cannot bind %s:%d: %w (is another TUI already running with --control-port %d?)", Host, port, err, port)
		}
		return l, nil
	}
	// Auto-assign, retrying in the vanishingly unlikely case the kernel hands
	// out the reserved port.
	for attempt := 0; attempt < 8; attempt++ {
		l, err := net.Listen("tcp", Host+":0")
		if err != nil {
			return nil, fmt.Errorf("control: cannot bind an auto-assigned port on %s: %w", Host, err)
		}
		if l.Addr().(*net.TCPAddr).Port != ReservedPort {
			return l, nil
		}
		l.Close()
	}
	return nil, fmt.Errorf("control: the kernel kept handing out the reserved port %d; pass an explicit --control-port", ReservedPort)
}

// Port is the bound port.
func (s *Server) Port() int { return s.port }

// BaseURL is the control API root.
func (s *Server) BaseURL() string { return fmt.Sprintf("http://%s:%d", Host, s.port) }

// Token is the bearer token. Exposed for in-process tests only — it is never
// printed to the terminal and never logged.
func (s *Server) Token() string { return s.token }

// WatchTermination removes the discovery file when the process is told to
// terminate, then asks the caller to quit — a deferred Stop only covers a normal
// exit, and the file left behind still reads as live.
//
// sigs is injected so tests drive this without signalling a real process. It
// returns when sigs is closed, so the caller can unwind it with signal.Stop.
func (s *Server) WatchTermination(sigs <-chan os.Signal, quit func()) {
	sig, ok := <-sigs
	if !ok {
		return
	}
	s.debugf("received %v — removing %s before exiting", sig, s.discoveryPath)
	if err := s.Stop(); err != nil {
		logf("%v", err)
	}
	if quit != nil {
		quit()
	}
}

// Stop shuts the listener down and removes the discovery file.
func (s *Server) Stop() error {
	s.stopOnce.Do(func() { close(s.done) })
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	shutdownErr := s.http.Shutdown(ctx)
	removeErr := RemoveInfo(s.pid)
	s.debugf("stopped")
	if shutdownErr != nil {
		return fmt.Errorf("control: shutdown failed: %w", shutdownErr)
	}
	if removeErr != nil {
		return removeErr
	}
	return nil
}

// ── plumbing ────────────────────────────────────────────────────────

type apiError struct {
	Code      string    `json:"code"`
	Message   string    `json:"message"`
	Hint      string    `json:"hint,omitempty"`
	Screen    string    `json:"screen,omitempty"`
	ElapsedMS int64     `json:"elapsed_ms,omitempty"`
	State     *Snapshot `json:"state,omitempty"`
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		// The response is already committed; nothing actionable remains but to
		// leave a trace for whoever reads the log.
		logf("[control] failed to encode response: %v", err)
	}
}

func writeErr(w http.ResponseWriter, status int, e apiError) {
	writeJSON(w, status, map[string]apiError{"error": e})
}

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		header := r.Header.Get("Authorization")
		prefix := AuthScheme + " "
		ok := strings.HasPrefix(header, prefix) &&
			subtle.ConstantTimeCompare([]byte(strings.TrimPrefix(header, prefix)), []byte(s.token)) == 1
		if !ok {
			s.debugf("rejected %s %s: bad or missing token", r.Method, r.URL.Path)
			writeErr(w, http.StatusUnauthorized, apiError{
				Code:    "unauthorized",
				Message: "missing or invalid bearer token",
				Hint:    "send Authorization: Bearer <token>, reading the token from the TUI control discovery file",
			})
			return
		}
		next(w, r)
	}
}

func (s *Server) decode(w http.ResponseWriter, r *http.Request, dst any) bool {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, apiError{
			Code:    "method_not_allowed",
			Message: fmt.Sprintf("%s requires POST, got %s", r.URL.Path, r.Method),
		})
		return false
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "bad_request",
			Message: fmt.Sprintf("cannot parse the request body: %v", err),
			Hint:    "send a JSON object with only the documented fields",
		})
		return false
	}
	return true
}

func (s *Server) requireGet(w http.ResponseWriter, r *http.Request) bool {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, apiError{
			Code:    "method_not_allowed",
			Message: fmt.Sprintf("%s requires GET, got %s", r.URL.Path, r.Method),
		})
		return false
	}
	return true
}

// send injects msgs into the live program and waits for them to be handled and
// rendered, so a caller that immediately reads /screen sees the whole batch.
//
// Program.Send only queues the message, so waiting for "some newer frame" is
// not enough: a spinner tick, or the first key of a three-key batch, satisfies
// it while the rest are still in flight. A trailing MarkMsg is queued behind
// the batch, and Bubble Tea's in-order processing makes "the mark has been
// rendered" mean "every key before it has been handled and drawn".
func (s *Server) send(msgs []tea.Msg, delay time.Duration) (seq int, settled bool) {
	s.injectMu.Lock()
	mark := s.state.NextMark()
	for i, msg := range msgs {
		if i > 0 && delay > 0 {
			time.Sleep(delay)
		}
		s.sender.Send(msg)
	}
	s.sender.Send(MarkMsg{ID: mark})
	s.injectMu.Unlock()
	return s.settle(mark)
}

// injectable reports whether the program can receive messages, writing the
// refusal itself when it cannot.
func (s *Server) injectable(w http.ResponseWriter) bool {
	if s.running.Load() {
		return true
	}
	s.debugf("refused injection: the Bubble Tea program is not consuming messages")
	writeErr(w, http.StatusServiceUnavailable, apiError{
		Code: "not_running",
		Message: "the TUI is not accepting input: its event loop is not running " +
			"(it is still starting up, or the user has quit it)",
		Hint: "check GET " + APIPrefix + "/status; if running is false, start a new TUI with the control API enabled",
	})
	return false
}

// settle waits up to renderSettleTimeout for the batch's mark to be rendered.
// settled=false means the model was too busy to confirm within the window — the
// keys are queued, but the caller must re-read the screen rather than assume.
func (s *Server) settle(mark int64) (seq int, settled bool) {
	deadline := time.Now().Add(renderSettleTimeout)
	for {
		ch := s.state.Changed()
		_, seq, _ = s.state.Current()
		if s.state.RenderedMark() >= mark {
			return seq, true
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			s.debugf("settle: mark %d not rendered within %s; the program may be busy", mark, renderSettleTimeout)
			return seq, false
		}
		timer := time.NewTimer(remaining)
		select {
		case <-ch:
		case <-timer.C:
		}
		timer.Stop()
	}
}

// ── handlers ────────────────────────────────────────────────────────

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	if !s.requireGet(w, r) {
		return
	}
	_, seq, snap := s.state.Current()
	cols, rows := s.state.Size()
	if snap.VisibleAgentIDs == nil {
		snap.VisibleAgentIDs = []string{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"service":     ServiceID,
		"api_version": APIVersion,
		"version":     s.version,
		"pid":         s.pid,
		"running":     s.running.Load(),
		"uptime_ms":   s.state.UptimeMS(),
		"cols":        cols,
		"rows":        rows,
		"frame_seq":   seq,
		"state":       snap,
	})
}

func (s *Server) handleScreen(w http.ResponseWriter, r *http.Request) {
	if !s.requireGet(w, r) {
		return
	}
	format := r.URL.Query().Get("format")
	if format == "" {
		format = "plain"
	}
	if format != "plain" && format != "ansi" {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "bad_format",
			Message: fmt.Sprintf("unknown format %q", format),
			Hint:    "use format=plain (ANSI stripped, the default) or format=ansi",
		})
		return
	}
	raw, seq, _ := s.state.Current()
	screen := raw
	if format == "plain" {
		screen = PlainScreen(raw)
	}
	cols, rows := s.state.Size()
	writeJSON(w, http.StatusOK, map[string]any{
		"format": format,
		"seq":    seq,
		"cols":   cols,
		"rows":   rows,
		"lines":  countLines(screen),
		"screen": screen,
	})
}

type keysRequest struct {
	Keys    []string `json:"keys"`
	DelayMS int      `json:"delay_ms"`
}

func (s *Server) handleKeys(w http.ResponseWriter, r *http.Request) {
	var req keysRequest
	if !s.decode(w, r, &req) {
		return
	}
	if len(req.Keys) == 0 {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "no_keys",
			Message: "keys is empty",
			Hint:    `send {"keys": ["tab", "down", "enter"]}`,
		})
		return
	}
	if len(req.Keys) > 100 {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "too_many_keys",
			Message: fmt.Sprintf("%d keys in one request; the cap is 100", len(req.Keys)),
			Hint:    "split the sequence across several calls, reading the screen in between",
		})
		return
	}
	if err := checkDelayBudget(req.DelayMS, len(req.Keys)); err != nil {
		writeErr(w, http.StatusBadRequest, *err)
		return
	}

	msgs := make([]tea.Msg, 0, len(req.Keys))
	for _, name := range req.Keys {
		key, err := KeyMsgFor(name)
		if err != nil {
			writeErr(w, http.StatusBadRequest, apiError{
				Code:    "unknown_key",
				Message: err.Error(),
				Hint:    "supported names: " + strings.Join(SupportedKeys(), ", "),
			})
			return
		}
		msgs = append(msgs, key)
	}

	if !s.injectable(w) {
		return
	}
	s.debugf("inject: keys %v (delay %dms)", req.Keys, req.DelayMS)
	seq, settled := s.send(msgs, time.Duration(req.DelayMS)*time.Millisecond)
	writeJSON(w, http.StatusOK, map[string]any{
		"sent":    len(msgs),
		"keys":    req.Keys,
		"seq":     seq,
		"settled": settled,
	})
}

type textRequest struct {
	Text    string `json:"text"`
	DelayMS int    `json:"delay_ms"`
}

func (s *Server) handleText(w http.ResponseWriter, r *http.Request) {
	var req textRequest
	if !s.decode(w, r, &req) {
		return
	}
	if req.Text == "" {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "no_text",
			Message: "text is empty",
			Hint:    `send {"text": "triage my inbox"}`,
		})
		return
	}
	if err := checkDelayBudget(req.DelayMS, len([]rune(req.Text))); err != nil {
		writeErr(w, http.StatusBadRequest, *err)
		return
	}

	keys := TextKeyMsgs(req.Text)
	msgs := make([]tea.Msg, 0, len(keys))
	for _, k := range keys {
		msgs = append(msgs, k)
	}
	if !s.injectable(w) {
		return
	}
	s.debugf("inject: text %q (%d runes)", req.Text, len(keys))
	seq, settled := s.send(msgs, time.Duration(req.DelayMS)*time.Millisecond)
	writeJSON(w, http.StatusOK, map[string]any{
		"sent_runes": len(keys),
		"seq":        seq,
		"settled":    settled,
	})
}

type waitRequest struct {
	Contains string         `json:"contains"`
	Absent   string         `json:"absent"`
	State    map[string]any `json:"state"`
	// Pointer so "omitted" (use the default) is distinguishable from an
	// explicit 0, which is a client bug: the caller's own HTTP timeout would be
	// far shorter than the wait, turning a reportable 408 into a dead socket.
	TimeoutMS *int `json:"timeout_ms"`
}

func (s *Server) handleWait(w http.ResponseWriter, r *http.Request) {
	var req waitRequest
	if !s.decode(w, r, &req) {
		return
	}
	if req.Contains == "" && req.Absent == "" && len(req.State) == 0 {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "no_condition",
			Message: "nothing to wait for",
			Hint:    `pass "contains", "absent", or "state" (they are ANDed)`,
		})
		return
	}
	if err := validateStateMatcher(req.State); err != nil {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "bad_state_matcher",
			Message: err.Error(),
			Hint:    "supported state keys: " + strings.Join(stateMatcherKeys(), ", "),
		})
		return
	}

	timeout := defaultWaitTimeout
	if req.TimeoutMS != nil {
		if *req.TimeoutMS <= 0 {
			writeErr(w, http.StatusBadRequest, apiError{
				Code:    "bad_timeout",
				Message: fmt.Sprintf("timeout_ms must be positive, got %d", *req.TimeoutMS),
				Hint:    fmt.Sprintf("omit timeout_ms for the %s default, or pass a positive value up to %s", defaultWaitTimeout, maxWaitTimeout),
			})
			return
		}
		timeout = time.Duration(*req.TimeoutMS) * time.Millisecond
	}
	if timeout > maxWaitTimeout {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "timeout_too_long",
			Message: fmt.Sprintf("timeout_ms %d exceeds the %s cap", *req.TimeoutMS, maxWaitTimeout),
		})
		return
	}

	start := time.Now()
	deadline := start.Add(timeout)
	for {
		// Take the wakeup channel BEFORE checking, or a change landing between
		// the check and the select is lost and the wait hangs to its deadline.
		ch := s.state.Changed()
		raw, seq, snap := s.state.Current()
		screen := PlainScreen(raw)
		if matchesWait(req, screen, snap) {
			elapsed := time.Since(start).Milliseconds()
			s.debugf("wait: matched after %dms (seq %d)", elapsed, seq)
			writeJSON(w, http.StatusOK, map[string]any{
				"matched":    true,
				"elapsed_ms": elapsed,
				"seq":        seq,
				"state":      snap,
				"screen":     screen,
			})
			return
		}

		remaining := time.Until(deadline)
		if remaining <= 0 {
			elapsed := time.Since(start).Milliseconds()
			s.debugf("wait: TIMEOUT after %dms; condition %s; screen was:\n%s",
				elapsed, describeWait(req), screen)
			writeErr(w, http.StatusRequestTimeout, apiError{
				Code:      "wait_timeout",
				Message:   fmt.Sprintf("timed out after %dms waiting for %s", elapsed, describeWait(req)),
				Hint:      "the screen field shows what the TUI was actually displaying when the wait expired",
				Screen:    screen,
				ElapsedMS: elapsed,
				State:     &snap,
			})
			return
		}

		timer := time.NewTimer(remaining)
		select {
		case <-ch:
		case <-timer.C:
		case <-r.Context().Done():
			timer.Stop()
			s.debugf("wait: client disconnected after %dms", time.Since(start).Milliseconds())
			return
		case <-s.done:
			timer.Stop()
			s.debugf("wait: aborted after %dms — the TUI is shutting down", time.Since(start).Milliseconds())
			writeErr(w, http.StatusServiceUnavailable, apiError{
				Code:    "shutting_down",
				Message: "the GAIA TUI is shutting down, so the wait was abandoned",
				Hint:    "start a new TUI with the control API enabled before driving it again",
				Screen:  screen,
			})
			return
		}
		timer.Stop()
	}
}

func (s *Server) handleFrames(w http.ResponseWriter, r *http.Request) {
	if !s.requireGet(w, r) {
		return
	}
	since, err := intParam(r, "since", 0)
	if err != nil {
		writeErr(w, http.StatusBadRequest, apiError{Code: "bad_param", Message: err.Error()})
		return
	}
	limit, err := intParam(r, "limit", 20)
	if err != nil {
		writeErr(w, http.StatusBadRequest, apiError{Code: "bad_param", Message: err.Error()})
		return
	}
	frames, latest, truncated := s.state.Frames(since, limit)
	if frames == nil {
		frames = []Frame{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"frames":     frames,
		"latest_seq": latest,
		"truncated":  truncated,
	})
}

type resizeRequest struct {
	Cols int `json:"cols"`
	Rows int `json:"rows"`
}

func (s *Server) handleResize(w http.ResponseWriter, r *http.Request) {
	var req resizeRequest
	if !s.decode(w, r, &req) {
		return
	}
	if req.Cols < 20 || req.Cols > 500 || req.Rows < 5 || req.Rows > 200 {
		writeErr(w, http.StatusBadRequest, apiError{
			Code:    "bad_size",
			Message: fmt.Sprintf("%dx%d is out of range", req.Cols, req.Rows),
			Hint:    "cols must be 20-500 and rows 5-200",
		})
		return
	}
	// Never lay out WIDER or TALLER than the terminal currently is. A synthetic
	// WindowSizeMsg only tells the model a size; it cannot make the real
	// terminal bigger. Asking for 200x55 on a 120x42 terminal makes the model
	// emit 200-column lines that the terminal hard-wraps, which shreds the
	// visible frame (blank screen, duplicated status line, lost scrollback)
	// while /screen still reports the clean logical frame — so the damage is
	// invisible to the very API you would test with.
	// Only when a physical viewport exists: headless runs (tests, CI) have no
	// terminal to overflow, and must stay free to lay out any size they like.
	if curCols, curRows := s.state.Size(); attachedToTerminal() &&
		curCols > 0 && curRows > 0 &&
		(req.Cols > curCols || req.Rows > curRows) {
		writeErr(w, http.StatusConflict, apiError{
			Code: "resize_exceeds_terminal",
			Message: fmt.Sprintf(
				"asked for %dx%d but the terminal is %dx%d; enlarging past it would corrupt the visible screen",
				req.Cols, req.Rows, curCols, curRows),
			Hint: "resize the real terminal window first, or request a size within " +
				fmt.Sprintf("%dx%d", curCols, curRows),
		})
		return
	}
	if !s.injectable(w) {
		return
	}
	s.debugf("inject: resize %dx%d", req.Cols, req.Rows)
	seq, settled := s.send([]tea.Msg{tea.WindowSizeMsg{Width: req.Cols, Height: req.Rows}}, 0)
	cols, rows := s.state.Size()
	if settled && (cols != req.Cols || rows != req.Rows) {
		// The model was reached but is laid out for a different size — report
		// it rather than echoing numbers that imply the resize took.
		writeErr(w, http.StatusConflict, apiError{
			Code: "resize_not_applied",
			Message: fmt.Sprintf("asked for %dx%d but the TUI is laid out for %dx%d",
				req.Cols, req.Rows, cols, rows),
			Hint: "a real terminal resize may have overridden it; try again",
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"cols":    cols,
		"rows":    rows,
		"seq":     seq,
		"settled": settled,
	})
}

func (s *Server) handleNotFound(w http.ResponseWriter, r *http.Request) {
	writeErr(w, http.StatusNotFound, apiError{
		Code:    "not_found",
		Message: fmt.Sprintf("no control endpoint at %s", r.URL.Path),
		Hint: "available endpoints: " + strings.Join([]string{
			"GET " + APIPrefix + "/status",
			"GET " + APIPrefix + "/screen",
			"POST " + APIPrefix + "/keys",
			"POST " + APIPrefix + "/text",
			"POST " + APIPrefix + "/wait",
			"GET " + APIPrefix + "/frames",
			"POST " + APIPrefix + "/resize",
		}, ", "),
	})
}

// maxInjectionDelay bounds how long one request may spend typing. Clients set
// their HTTP timeout from a fixed budget, so an unbounded delay_ms * count
// would time the caller out mid-batch and leave it unable to tell what landed.
const maxInjectionDelay = 10 * time.Second

func checkDelayBudget(delayMS, count int) *apiError {
	if delayMS < 0 || delayMS > 2000 {
		return &apiError{
			Code:    "bad_delay",
			Message: fmt.Sprintf("delay_ms %d is out of range", delayMS),
			Hint:    "delay_ms must be between 0 and 2000",
		}
	}
	total := time.Duration(delayMS) * time.Millisecond * time.Duration(max(count-1, 0))
	if total > maxInjectionDelay {
		return &apiError{
			Code: "delay_budget_exceeded",
			Message: fmt.Sprintf("delay_ms %d across %d items would take %s, over the %s cap",
				delayMS, count, total, maxInjectionDelay),
			Hint: "lower delay_ms or split the batch, so the request finishes inside the client's timeout",
		}
	}
	return nil
}

// ── matching ────────────────────────────────────────────────────────

func matchesWait(req waitRequest, screen string, snap Snapshot) bool {
	if req.Contains != "" && !strings.Contains(screen, req.Contains) {
		return false
	}
	if req.Absent != "" && strings.Contains(screen, req.Absent) {
		return false
	}
	for key, want := range req.State {
		if !matchesStateKey(key, want, snap) {
			return false
		}
	}
	return true
}

func matchesStateKey(key string, want any, snap Snapshot) bool {
	switch key {
	case "view":
		return want == snap.View
	case "agent":
		return want == snap.Agent
	case "hub_tab":
		return want == snap.HubTab
	case "selected_agent_id":
		return want == snap.SelectedAgentID
	case "overlay":
		return want == snap.Overlay
	case "streaming":
		return want == snap.Streaming
	case "filtering":
		return want == snap.Filtering
	case "can_return_to_hub":
		return want == snap.CanReturnToHub
	case "hub_tab_index":
		n, ok := want.(float64)
		return ok && int(n) == snap.HubTabIndex
	case "visible_contains":
		s, ok := want.(string)
		if !ok {
			return false
		}
		for _, id := range snap.VisibleAgentIDs {
			if id == s {
				return true
			}
		}
		return false
	}
	return false
}

// stateMatcherTypes documents the accepted type for each state matcher key, so
// a typo or a wrong type is a 400 instead of a wait that can never succeed.
var stateMatcherTypes = map[string]string{
	"view":              "string",
	"agent":             "string",
	"hub_tab":           "string",
	"selected_agent_id": "string",
	"overlay":           "string",
	"visible_contains":  "string",
	"streaming":         "bool",
	"filtering":         "bool",
	"can_return_to_hub": "bool",
	"hub_tab_index":     "number",
}

func stateMatcherKeys() []string {
	keys := make([]string, 0, len(stateMatcherTypes))
	for k := range stateMatcherTypes {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func validateStateMatcher(matcher map[string]any) error {
	for key, want := range matcher {
		wantType, ok := stateMatcherTypes[key]
		if !ok {
			return fmt.Errorf("unknown state key %q", key)
		}
		switch wantType {
		case "string":
			if _, ok := want.(string); !ok {
				return fmt.Errorf("state key %q expects a string, got %T", key, want)
			}
		case "bool":
			if _, ok := want.(bool); !ok {
				return fmt.Errorf("state key %q expects a boolean, got %T", key, want)
			}
		case "number":
			if _, ok := want.(float64); !ok {
				return fmt.Errorf("state key %q expects a number, got %T", key, want)
			}
		}
	}
	return nil
}

func describeWait(req waitRequest) string {
	var parts []string
	if req.Contains != "" {
		parts = append(parts, fmt.Sprintf("screen containing %q", req.Contains))
	}
	if req.Absent != "" {
		parts = append(parts, fmt.Sprintf("screen no longer containing %q", req.Absent))
	}
	keys := make([]string, 0, len(req.State))
	for k := range req.State {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("state %s=%v", k, req.State[k]))
	}
	return strings.Join(parts, " and ")
}

// countLines reports rendered lines; an empty screen is 0, not the 1 that a
// naive Split would report.
func countLines(screen string) int {
	if screen == "" {
		return 0
	}
	return strings.Count(screen, "\n") + 1
}

func intParam(r *http.Request, name string, def int) (int, error) {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return def, nil
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("%s must be an integer, got %q", name, raw)
	}
	if n < 0 {
		return 0, fmt.Errorf("%s must not be negative, got %d", name, n)
	}
	return n, nil
}
