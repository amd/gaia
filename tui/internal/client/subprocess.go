package client

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

// detectLemonadeURL probes common Lemonade Server ports and returns the first reachable URL.
func detectLemonadeURL() string {
	ports := []string{"13305", "8000"}
	client := &http.Client{Timeout: 2 * time.Second}

	for _, port := range ports {
		url := "http://localhost:" + port + "/api/v1"
		resp, err := client.Get(url + "/models")
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				return url
			}
		}
	}
	return ""
}

// procHandle owns one child process so exactly one Wait() ever runs for it —
// the reader goroutine, a cancellation kill, and Close() all funnel through it.
type procHandle struct {
	cmd      *exec.Cmd
	waitOnce sync.Once
	state    *os.ProcessState
}

// wait reaps the child and returns its final state (nil if it was never started).
func (p *procHandle) wait() *os.ProcessState {
	p.waitOnce.Do(func() {
		_ = p.cmd.Wait()
		p.state = p.cmd.ProcessState
	})
	return p.state
}

// kill terminates the child and reaps it.
func (p *procHandle) kill() {
	if p.cmd.Process != nil {
		_ = p.cmd.Process.Kill()
	}
	p.wait()
}

// SubprocessClient communicates with a local agent binary via stdin/stdout JSONL.
// Send() calls must be serialized — do not overlap two Send() calls.
type SubprocessClient struct {
	path  string
	args  []string
	debug bool

	mu      sync.Mutex
	proc    *procHandle
	stdin   io.WriteCloser
	stdout  *bufio.Scanner
	stderr  *bytes.Buffer
	started bool
}

// NewSubprocessClient creates a client for an agent binary and its arguments.
//
// argv is taken pre-split: a single command string would have to be re-split on
// whitespace, which corrupts any path containing a space. Callers holding one
// string (e.g. `gaia tui chat --subprocess "..."`) split it with
// SplitCommandLine, which honours quoting.
func NewSubprocessClient(path string, args []string, debug bool) *SubprocessClient {
	return &SubprocessClient{
		path:  path,
		args:  args,
		debug: debug,
	}
}

// start spawns the subprocess if not already running.
func (s *SubprocessClient) start() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.started {
		return nil
	}
	if s.path == "" {
		return fmt.Errorf("no agent binary was given, so nothing can be launched")
	}

	cmd := exec.Command(s.path, s.args...)
	s.stderr = &bytes.Buffer{}
	cmd.Stderr = s.stderr

	// Auto-detect Lemonade URL if not set in environment
	if os.Getenv("LEMONADE_BASE_URL") == "" {
		if url := detectLemonadeURL(); url != "" {
			cmd.Env = append(os.Environ(), "LEMONADE_BASE_URL="+url)
			if s.debug {
				fmt.Fprintf(os.Stderr, "[DEBUG] Auto-detected Lemonade at %s\n", url)
			}
		}
	}

	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdin pipe: %w", err)
	}
	s.stdin = stdinPipe

	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdout pipe: %w", err)
	}

	scanner := bufio.NewScanner(stdoutPipe)
	// 1MB buffer for large tool outputs
	scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024)
	s.stdout = scanner

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start agent %q: %w", s.path, err)
	}

	s.proc = &procHandle{cmd: cmd}
	s.started = true
	return nil
}

// Send writes a query to stdin and returns a channel of parsed events.
func (s *SubprocessClient) Send(ctx context.Context, query string) (<-chan interface{}, error) {
	if err := s.start(); err != nil {
		return nil, err
	}

	if _, err := fmt.Fprintln(s.stdin, query); err != nil {
		return nil, fmt.Errorf("failed to write to agent stdin: %w", err)
	}

	// Capture references under lock so the goroutine doesn't race with Close().
	s.mu.Lock()
	scanner := s.stdout
	proc := s.proc
	stderrBuf := s.stderr
	debug := s.debug
	s.mu.Unlock()

	ch := make(chan interface{}, 32)

	// A cancelled turn must actually stop the child. Abandoning the read while
	// the agent keeps writing leaves the tail of this turn's output in the pipe,
	// which the NEXT turn would read as its own — so the process is killed and
	// respawned instead.
	turnDone := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			s.terminate()
		case <-turnDone:
		}
	}()

	go func() {
		defer close(ch)
		defer close(turnDone)

		emit := func(evt interface{}) bool {
			select {
			case ch <- evt:
				return true
			case <-ctx.Done():
				return false
			}
		}

		for scanner.Scan() {
			line := scanner.Bytes()
			if len(line) == 0 {
				continue
			}

			evt, err := event.ParseEvent(line)
			if err != nil {
				// Visible, not dropped: a status warning keeps the turn alive
				// while making a bad producer obvious.
				if debug {
					fmt.Fprintf(os.Stderr, "[DEBUG] parse error: %v (line: %s)\n", err, string(line))
				}
				if !emit(event.StatusEvent{
					Type:    "status",
					Status:  "warning",
					Message: fmt.Sprintf("unreadable agent event (%v): %s", err, truncateLine(string(line))),
				}) {
					return
				}
				continue
			}

			// Skip stale "complete" status from a previous turn's trailing event
			if se, ok := evt.(event.StatusEvent); ok && se.Status == "complete" {
				continue
			}

			if !emit(evt) {
				return
			}

			// Turn boundary — stop reading after terminal events.
			switch evt.(type) {
			case event.AnswerEvent:
				return
			case event.AgentErrorEvent:
				return
			case event.DoneEvent:
				return
			}
		}

		// Scanner stopped — check for read errors or unexpected process exit.
		if err := scanner.Err(); err != nil {
			emit(event.AgentErrorEvent{
				Type:    "agent_error",
				Content: fmt.Sprintf("agent stdout read error: %v", err),
			})
			return
		}

		// Process exited — reap it to get the exit code, then report if non-zero.
		state := proc.wait()
		if state != nil && !state.Success() {
			stderrContent := stderrBuf.String()
			msg := fmt.Sprintf("agent process exited with code %d", state.ExitCode())
			if stderrContent != "" {
				msg += "\n" + stderrContent
			}
			emit(event.AgentErrorEvent{
				Type:    "agent_error",
				Content: msg,
			})
		}
	}()

	return ch, nil
}

// detach clears the client's process state and hands back what it was holding.
func (s *SubprocessClient) detach() (*procHandle, io.WriteCloser) {
	s.mu.Lock()
	defer s.mu.Unlock()

	proc, stdin := s.proc, s.stdin
	s.proc = nil
	s.stdin = nil
	s.stdout = nil
	s.stderr = nil
	s.started = false
	return proc, stdin
}

// terminate kills the child and resets the client so the next Send respawns it
// against a clean pipe.
func (s *SubprocessClient) terminate() {
	proc, stdin := s.detach()
	if stdin != nil {
		stdin.Close()
	}
	if proc != nil {
		proc.kill()
	}
}

// Close terminates the subprocess.
func (s *SubprocessClient) Close() error {
	proc, stdin := s.detach()
	// Close stdin to signal EOF to the child process.
	if stdin != nil {
		stdin.Close()
	}
	if proc != nil {
		proc.wait()
	}
	return nil
}

func truncateLine(s string) string {
	const limit = 200
	if len(s) <= limit {
		return s
	}
	return s[:limit] + "…"
}
