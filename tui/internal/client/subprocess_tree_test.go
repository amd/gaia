package client

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

// buildRepoMockAgent builds tui/test/mockagent — the shared test double, not
// an inline one — so the multi-process shape exercised here is the same one
// the TUI's end-to-end tests drive.
func buildRepoMockAgent(t *testing.T) string {
	t.Helper()
	name := "mockagent"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	bin := filepath.Join(t.TempDir(), name)
	goExe := "go"
	if p, err := exec.LookPath("go"); err == nil {
		goExe = p
	}
	cmd := exec.Command(goExe, "build", "-o", bin, "github.com/amd/gaia/tui/test/mockagent")
	cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build mockagent: %v\n%s", err, out)
	}
	return bin
}

func waitFor(t *testing.T, what string, timeout time.Duration, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out after %s waiting for %s", timeout, what)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// readPid waits for the pid file to name a live process other than `not`.
func readPid(t *testing.T, path string, not int) int {
	t.Helper()
	var pid int
	waitFor(t, "the agent to publish its pid", 15*time.Second, func() bool {
		b, err := os.ReadFile(path)
		if err != nil {
			return false
		}
		n, err := strconv.Atoi(strings.TrimSpace(string(b)))
		if err != nil || n <= 0 || n == not {
			return false
		}
		pid = n
		return true
	})
	return pid
}

// runTurn sends one query and collects the turn's events. A background
// context on purpose: the watchdog is a timer here, so ending the test's wait
// can never be mistaken for the user abandoning the turn.
func runTurn(t *testing.T, c *SubprocessClient, query string) []interface{} {
	t.Helper()
	ch, err := c.Send(context.Background(), query)
	if err != nil {
		t.Fatalf("Send(%q): %v", query, err)
	}
	var evts []interface{}
	timeout := time.After(30 * time.Second)
	for {
		select {
		case e, ok := <-ch:
			if !ok {
				return evts
			}
			evts = append(evts, e)
		case <-timeout:
			t.Fatalf("turn %q did not end within 30s", query)
		}
	}
}

func answerOf(evts []interface{}) string {
	for _, e := range evts {
		if a, ok := e.(event.AnswerEvent); ok {
			return a.Content
		}
	}
	return ""
}

func bootPID(c *SubprocessClient) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.proc == nil {
		return 0
	}
	return c.proc.cmd.Process.Pid
}

// childArgv is the argv the current child was actually launched with.
func childArgv(c *SubprocessClient) []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]string(nil), c.proc.cmd.Args...)
}

func isStarted(c *SubprocessClient) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.started
}

// startSlowTool begins a "slow tool" turn and returns once the tool is running.
func startSlowTool(t *testing.T, c *SubprocessClient, ctx context.Context) <-chan interface{} {
	t.Helper()
	ch, err := c.Send(ctx, "run the slow tool")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	select {
	case e := <-ch:
		if _, ok := e.(event.ToolStartEvent); !ok {
			t.Fatalf("first event = %T, want the slow tool starting", e)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("the slow tool never started")
	}
	return ch
}

// TestSubprocessProcessTree covers cancelling against an agent shaped like the
// released one: a bootloader whose child does the work and holds the pipes.
func TestSubprocessProcessTree(t *testing.T) {
	bin := buildRepoMockAgent(t)

	env := func(t *testing.T, toolMS int) (pidfile, sideEffect string) {
		dir := t.TempDir()
		pidfile = filepath.Join(dir, "agent.pid")
		sideEffect = filepath.Join(dir, "tool-ran")
		t.Setenv("MOCKAGENT_MULTIPROCESS", "1")
		t.Setenv("MOCKAGENT_PIDFILE", pidfile)
		t.Setenv("MOCKAGENT_SIDE_EFFECT", sideEffect)
		t.Setenv("MOCKAGENT_TOOL_MS", strconv.Itoa(toolMS))
		// Skip the Lemonade port probe; nothing here talks to a model.
		t.Setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
		return pidfile, sideEffect
	}

	// Without this the tests below prove nothing: a double where killing the
	// bootloader also kills the child cannot tell a tree kill from a bare one.
	t.Run("the double reproduces the one-file shape", func(t *testing.T) {
		pidfile, _ := env(t, 5000)
		cmd := exec.Command(bin)
		stdin, err := cmd.StdinPipe()
		if err != nil {
			t.Fatal(err)
		}
		if _, err := cmd.StdoutPipe(); err != nil {
			t.Fatal(err)
		}
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		child := readPid(t, pidfile, 0)
		if child == cmd.Process.Pid {
			t.Fatal("MOCKAGENT_MULTIPROCESS=1 served turns from the bootloader itself")
		}

		_ = cmd.Process.Kill()
		time.Sleep(500 * time.Millisecond)
		survived := daemon.PIDAlive(child)

		if p, err := os.FindProcess(child); err == nil {
			_ = p.Kill()
		}
		stdin.Close()
		_ = cmd.Wait()

		if !survived {
			t.Fatal("killing only the bootloader also killed its child — the double no longer " +
				"reproduces the one-file agent, so the tree-kill tests are vacuous")
		}
	})

	t.Run("abandoning a turn kills the whole tree before the tool finishes", func(t *testing.T) {
		pidfile, sideEffect := env(t, 1500)
		c := NewSubprocessClient(bin, nil, false)
		defer c.Close()

		ctx, cancel := context.WithCancel(context.Background())
		ch := startSlowTool(t, c, ctx)
		child := readPid(t, pidfile, 0)
		boot := bootPID(c)
		if boot == child {
			t.Fatal("expected the agent to run in a child of the started process")
		}
		t.Logf("bootloader pid %d, agent pid %d", boot, child)

		cancel()
		for range ch {
		}

		waitFor(t, "the agent child to die", 5*time.Second, func() bool { return !daemon.PIDAlive(child) })
		waitFor(t, "the bootloader to die", 5*time.Second, func() bool { return !daemon.PIDAlive(boot) })
		waitFor(t, "the client to drop the killed agent", 5*time.Second, func() bool { return !isStarted(c) })
		c.mu.Lock()
		pending := c.respawned
		c.mu.Unlock()
		if strings.Contains(pending, "failed") {
			t.Errorf("a clean tree kill was reported as a failure: %q", pending)
		}

		// Past the point the tool would have finished had anything survived.
		time.Sleep(2500 * time.Millisecond)
		if _, err := os.Stat(sideEffect); err == nil {
			t.Fatal("the cancelled tool call ran to completion")
		}
	})

	t.Run("a cooperative cancel stops the turn and keeps the process", func(t *testing.T) {
		pidfile, sideEffect := env(t, 10000)
		c := NewSubprocessClient(bin, nil, false)
		defer c.Close()

		if err := c.Cancel(context.Background()); err != nil {
			t.Fatalf("Cancel with nothing running must not be an error: %v", err)
		}

		ch := startSlowTool(t, c, context.Background())
		child := readPid(t, pidfile, 0)

		asked := time.Now()
		if err := c.Cancel(context.Background()); err != nil {
			t.Fatalf("Cancel: %v", err)
		}
		var got string
		for e := range ch {
			if a, ok := e.(event.AnswerEvent); ok {
				got = a.Content
			}
		}
		if got != "stopped" {
			t.Fatalf("the turn ended with %q, want the agent's own \"stopped\" answer", got)
		}
		if waited := time.Since(asked); waited > 3*time.Second {
			t.Fatalf("the turn took %s to stop after Cancel", waited)
		}
		if !daemon.PIDAlive(child) {
			t.Fatal("a cooperative cancel killed the agent")
		}

		if a := answerOf(runTurn(t, c, "report bypass")); !strings.HasPrefix(a, "bypass=false ") {
			t.Fatalf("follow-up turn answered %q", a)
		}
		if again := readPid(t, pidfile, 0); again != child {
			t.Fatalf("the follow-up ran on a new process (%d, was %d)", again, child)
		}
		if _, err := os.Stat(sideEffect); err == nil {
			t.Fatal("the cancelled tool call ran to completion")
		}
	})

	t.Run("a respawn keeps /bypass off and says what was lost", func(t *testing.T) {
		pidfile, _ := env(t, 10000)
		c := NewSubprocessClient(bin, []string{BypassPermissionsFlag}, false)
		defer c.Close()

		if a := answerOf(runTurn(t, c, "report bypass")); !strings.HasPrefix(a, "bypass=true ") {
			t.Fatalf("launched with the flag, agent reports %q", a)
		}
		if argv := childArgv(c); !slices.Contains(argv, BypassPermissionsFlag) {
			t.Fatalf("the first agent was not launched with %s: %v", BypassPermissionsFlag, argv)
		}
		if err := c.SetBypassPermissions(false); err != nil {
			t.Fatalf("SetBypassPermissions(false): %v", err)
		}
		if a := answerOf(runTurn(t, c, "report bypass")); !strings.HasPrefix(a, "bypass=false ") {
			t.Fatalf("after /bypass off, agent reports %q", a)
		}
		first := readPid(t, pidfile, 0)

		ctx, cancel := context.WithCancel(context.Background())
		ch := startSlowTool(t, c, ctx)
		cancel()
		for range ch {
		}
		waitFor(t, "the client to drop the killed agent", 5*time.Second, func() bool { return !isStarted(c) })

		evts := runTurn(t, c, "report bypass")
		if second := readPid(t, pidfile, first); second == first {
			t.Fatal("expected a new agent process after the hard stop")
		}
		if argv := childArgv(c); slices.Contains(argv, BypassPermissionsFlag) {
			t.Fatalf("the respawned agent was launched with %s after /bypass off: %v", BypassPermissionsFlag, argv)
		}
		if a := answerOf(evts); !strings.HasPrefix(a, "bypass=false ") {
			t.Fatalf("the respawned agent reports %q — /bypass off was reverted by the restart", a)
		}
		notice, ok := evts[0].(event.CanonicalNoticeEvent)
		if !ok {
			t.Fatalf("first event after the respawn = %T, want the restart notice", evts[0])
		}
		for _, want := range []string{"restarted", "Permission prompts are on"} {
			if !strings.Contains(notice.Text, want) {
				t.Errorf("restart notice %q does not say %q", notice.Text, want)
			}
		}
		if strings.Contains(notice.Text, "failed") {
			t.Errorf("a clean restart reported a failed stop: %q", notice.Text)
		}
		if !c.BypassAtLaunch() {
			t.Error("BypassAtLaunch must keep describing the launch, not the current mode")
		}
	})

	t.Run("a message sent straight after a hard stop starts the replacement", func(t *testing.T) {
		env(t, 10000)
		c := NewSubprocessClient(bin, nil, false)
		defer c.Close()

		runTurn(t, c, "report bypass")
		ctx, cancel := context.WithCancel(context.Background())
		ch := startSlowTool(t, c, ctx)
		cancel()
		// No drain and no wait: the TUI drops its read on a hard stop, and the
		// user can type the next message before the teardown has finished.
		evts := runTurn(t, c, "report bypass")
		for range ch {
		}
		if _, ok := evts[0].(event.CanonicalNoticeEvent); !ok {
			t.Fatalf("first event = %T, want the restart notice", evts[0])
		}
		if a := answerOf(evts); !strings.HasPrefix(a, "bypass=false ") {
			t.Fatalf("the replacement answered %q", a)
		}
	})

	t.Run("a second message while a turn runs is refused", func(t *testing.T) {
		env(t, 10000)
		c := NewSubprocessClient(bin, nil, false)
		defer c.Close()

		ch := startSlowTool(t, c, context.Background())
		if _, err := c.Send(context.Background(), "hello"); err == nil ||
			!strings.Contains(err.Error(), "still running") {
			t.Fatalf("overlapping Send: err = %v, want a refusal", err)
		}
		if err := c.Cancel(context.Background()); err != nil {
			t.Fatalf("Cancel: %v", err)
		}
		for range ch {
		}
	})

	t.Run("/bypass with no agent running applies to the next one", func(t *testing.T) {
		env(t, 10000)
		c := NewSubprocessClient(bin, []string{BypassPermissionsFlag}, false)
		defer c.Close()

		if err := c.SetBypassPermissions(false); err != nil {
			t.Fatalf("SetBypassPermissions before any turn: %v", err)
		}
		if a := answerOf(runTurn(t, c, "report bypass")); !strings.HasPrefix(a, "bypass=false ") {
			t.Fatalf("agent reports %q", a)
		}
	})
}

func TestSpawnArgsFollowTheSessionMode(t *testing.T) {
	launch := []string{"--dev", BypassPermissionsFlag, UseClaudeFlag}
	c := NewSubprocessClient("agent", launch, false)

	if got, want := c.spawnArgs(false), []string{"--dev", UseClaudeFlag}; !reflect.DeepEqual(got, want) {
		t.Errorf("spawnArgs(false) = %v, want %v", got, want)
	}
	if got, want := c.spawnArgs(true), []string{"--dev", UseClaudeFlag, BypassPermissionsFlag}; !reflect.DeepEqual(got, want) {
		t.Errorf("spawnArgs(true) = %v, want %v", got, want)
	}
	if !reflect.DeepEqual(c.args, []string{"--dev", BypassPermissionsFlag, UseClaudeFlag}) {
		t.Errorf("spawnArgs mutated the launch argv: %v", c.args)
	}
}

func TestFailedBypassWriteKeepsRespawnInSafeMode(t *testing.T) {
	for _, enable := range []bool{false, true} {
		t.Run(fmt.Sprintf("enable=%t", enable), func(t *testing.T) {
			args := []string{}
			if !enable {
				args = append(args, BypassPermissionsFlag)
			}
			c := NewSubprocessClient("agent", args, false)
			reader, writer, err := os.Pipe()
			if err != nil {
				t.Fatal(err)
			}
			reader.Close()
			writer.Close()
			c.started = true
			c.stdin = writer

			if err := c.SetBypassPermissions(enable); err == nil {
				t.Fatal("closed stdin must report an undelivered control message")
			}
			if got := c.spawnArgs(c.bypass); slices.Contains(got, BypassPermissionsFlag) {
				t.Fatalf("respawn would enable bypass after a failed control write: %v", got)
			}
		})
	}
}
