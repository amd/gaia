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
	"strings"
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

// SubprocessClient communicates with a C++ agent via stdin/stdout JSONL.
// Send() calls must be serialized — do not overlap two Send() calls.
type SubprocessClient struct {
	cmdLine string
	debug   bool

	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	stdout  *bufio.Scanner
	stderr  *bytes.Buffer
	started bool
}

// NewSubprocessClient creates a client from a command string like "./gaia-bash --json-events".
func NewSubprocessClient(cmdLine string, debug bool) *SubprocessClient {
	return &SubprocessClient{
		cmdLine: cmdLine,
		debug:   debug,
	}
}

// start spawns the subprocess if not already running.
func (s *SubprocessClient) start() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.started {
		return nil
	}

	parts := strings.Fields(s.cmdLine)
	if len(parts) == 0 {
		return fmt.Errorf("empty subprocess command")
	}

	s.cmd = exec.Command(parts[0], parts[1:]...)
	s.stderr = &bytes.Buffer{}
	s.cmd.Stderr = s.stderr

	// Auto-detect Lemonade URL if not set in environment
	if os.Getenv("LEMONADE_BASE_URL") == "" {
		if url := detectLemonadeURL(); url != "" {
			s.cmd.Env = append(os.Environ(), "LEMONADE_BASE_URL="+url)
			if s.debug {
				fmt.Fprintf(os.Stderr, "[DEBUG] Auto-detected Lemonade at %s\n", url)
			}
		}
	}

	stdinPipe, err := s.cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdin pipe: %w", err)
	}
	s.stdin = stdinPipe

	stdoutPipe, err := s.cmd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("failed to create stdout pipe: %w", err)
	}

	scanner := bufio.NewScanner(stdoutPipe)
	// 1MB buffer for large tool outputs
	scanner.Buffer(make([]byte, 0, 1024*1024), 1024*1024)
	s.stdout = scanner

	if err := s.cmd.Start(); err != nil {
		return fmt.Errorf("failed to start subprocess %q: %w", parts[0], err)
	}

	s.started = true
	return nil
}

// Send writes a query to stdin and returns a channel of parsed events.
func (s *SubprocessClient) Send(ctx context.Context, query string) (<-chan interface{}, error) {
	if err := s.start(); err != nil {
		return nil, err
	}

	if _, err := fmt.Fprintln(s.stdin, query); err != nil {
		return nil, fmt.Errorf("failed to write to subprocess stdin: %w", err)
	}

	// Capture references under lock so the goroutine doesn't race with Close().
	s.mu.Lock()
	scanner := s.stdout
	cmd := s.cmd
	stderrBuf := s.stderr
	debug := s.debug
	s.mu.Unlock()

	ch := make(chan interface{}, 32)

	go func() {
		defer close(ch)

		for scanner.Scan() {
			line := scanner.Bytes()
			if len(line) == 0 {
				continue
			}

			evt, err := event.ParseEvent(line)
			if err != nil {
				if debug {
					fmt.Fprintf(os.Stderr, "[DEBUG] parse error: %v (line: %s)\n", err, string(line))
				}
				continue
			}

			// Skip stale "complete" status from a previous turn's trailing event
			if se, ok := evt.(event.StatusEvent); ok && se.Status == "complete" {
				continue
			}

			select {
			case ch <- evt:
			case <-ctx.Done():
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
			select {
			case ch <- event.AgentErrorEvent{
				Type:    "agent_error",
				Content: fmt.Sprintf("subprocess stdout read error: %v", err),
			}:
			case <-ctx.Done():
			}
			return
		}

		// Process exited — wait to get exit code, then report if non-zero.
		_ = cmd.Wait()
		if cmd.ProcessState != nil && !cmd.ProcessState.Success() {
			stderrContent := stderrBuf.String()
			msg := fmt.Sprintf("agent process exited with code %d", cmd.ProcessState.ExitCode())
			if stderrContent != "" {
				msg += "\n" + stderrContent
			}
			select {
			case ch <- event.AgentErrorEvent{
				Type:    "agent_error",
				Content: msg,
			}:
			case <-ctx.Done():
			}
		}
	}()

	return ch, nil
}

// Close terminates the subprocess.
func (s *SubprocessClient) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if !s.started {
		return nil
	}

	// Close stdin to signal EOF to the child process.
	if s.stdin != nil {
		s.stdin.Close()
	}

	if s.cmd != nil {
		s.cmd.Wait()
	}

	s.stdin = nil
	s.stdout = nil
	s.stderr = nil
	s.cmd = nil
	s.started = false
	return nil
}
