package client

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/event"
)

// buildMockAgent compiles a small Go program that reads stdin lines
// and emits JSONL events to stdout, simulating an agent backend.
func buildMockAgent(t *testing.T) string {
	t.Helper()

	src := `package main

import (
	"bufio"
	"fmt"
	"os"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		query := scanner.Text()
		fmt.Fprintf(os.Stderr, "got query: %s\n", query)

		fmt.Println("{\"type\":\"step\",\"step\":1,\"total\":3,\"status\":\"running\"}")
		fmt.Println("{\"type\":\"thinking\",\"content\":\"Let me think about this...\"}")
		fmt.Println("{\"type\":\"tool_start\",\"tool\":\"bash\",\"detail\":\"echo hello\"}")
		fmt.Println("{\"type\":\"tool_end\",\"success\":true}")
		fmt.Println("{\"type\":\"answer\",\"content\":\"Here is my answer\",\"steps\":1,\"tools_used\":1}")
	}
}
`
	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "mock_agent.go")
	if err := os.WriteFile(srcPath, []byte(src), 0644); err != nil {
		t.Fatalf("write mock agent source: %v", err)
	}

	binName := "mock_agent"
	if runtime.GOOS == "windows" {
		binName = "mock_agent.exe"
	}
	binPath := filepath.Join(tmpDir, binName)

	goExe := "go"
	if p, err := exec.LookPath("go"); err == nil {
		goExe = p
	}

	cmd := exec.Command(goExe, "build", "-o", binPath, srcPath)
	cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build mock agent: %v\n%s", err, out)
	}

	return binPath
}

func TestSubprocessClient_SendReceivesEvents(t *testing.T) {
	bin := buildMockAgent(t)

	c := NewSubprocessClient(bin, nil, true)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	ch, err := c.Send(ctx, "hello world")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}

	var events []interface{}
	for evt := range ch {
		events = append(events, evt)
	}

	if len(events) != 5 {
		t.Fatalf("expected 5 events, got %d: %+v", len(events), events)
	}

	// Verify types in order.
	if _, ok := events[0].(event.StepEvent); !ok {
		t.Errorf("event[0]: expected StepEvent, got %T", events[0])
	}
	if _, ok := events[1].(event.ThinkingEvent); !ok {
		t.Errorf("event[1]: expected ThinkingEvent, got %T", events[1])
	}
	if _, ok := events[2].(event.ToolStartEvent); !ok {
		t.Errorf("event[2]: expected ToolStartEvent, got %T", events[2])
	}
	if _, ok := events[3].(event.ToolEndEvent); !ok {
		t.Errorf("event[3]: expected ToolEndEvent, got %T", events[3])
	}
	if ans, ok := events[4].(event.AnswerEvent); !ok {
		t.Errorf("event[4]: expected AnswerEvent, got %T", events[4])
	} else {
		if ans.Content != "Here is my answer" {
			t.Errorf("answer content = %q, want %q", ans.Content, "Here is my answer")
		}
		if ans.Steps != 1 {
			t.Errorf("answer steps = %d, want 1", ans.Steps)
		}
		if ans.ToolsUsed != 1 {
			t.Errorf("answer tools_used = %d, want 1", ans.ToolsUsed)
		}
	}
}

func TestSubprocessClient_MultiTurn(t *testing.T) {
	bin := buildMockAgent(t)

	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// First turn.
	ch1, err := c.Send(ctx, "turn one")
	if err != nil {
		t.Fatalf("Send turn 1: %v", err)
	}
	count1 := 0
	for range ch1 {
		count1++
	}
	if count1 != 5 {
		t.Errorf("turn 1: expected 5 events, got %d", count1)
	}

	// Second turn — same process, reused.
	ch2, err := c.Send(ctx, "turn two")
	if err != nil {
		t.Fatalf("Send turn 2: %v", err)
	}
	count2 := 0
	for range ch2 {
		count2++
	}
	if count2 != 5 {
		t.Errorf("turn 2: expected 5 events, got %d", count2)
	}
}

func TestSubprocessClient_InvalidCommand(t *testing.T) {
	c := NewSubprocessClient("nonexistent_binary_xyz_12345", nil, false)
	defer c.Close()

	ctx := context.Background()
	_, err := c.Send(ctx, "hello")
	if err == nil {
		t.Fatal("expected error for invalid command, got nil")
	}
}

func TestSubprocessClient_EmptyBinaryPath(t *testing.T) {
	c := NewSubprocessClient("", nil, false)

	ctx := context.Background()
	_, err := c.Send(ctx, "hello")
	if err == nil {
		t.Fatal("expected error for empty command, got nil")
	}
}

func TestSubprocessClient_CloseBeforeSend(t *testing.T) {
	c := NewSubprocessClient("echo", nil, false)

	// Close without ever starting should be a no-op.
	if err := c.Close(); err != nil {
		t.Fatalf("Close before Send: %v", err)
	}
}

func TestSubprocessClient_ProcessExitWithError(t *testing.T) {
	// Build a mock that exits with code 1 immediately.
	src := `package main
import "os"
func main() { os.Exit(1) }
`
	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "exit_agent.go")
	if err := os.WriteFile(srcPath, []byte(src), 0644); err != nil {
		t.Fatalf("write source: %v", err)
	}

	binName := "exit_agent"
	if runtime.GOOS == "windows" {
		binName = "exit_agent.exe"
	}
	binPath := filepath.Join(tmpDir, binName)

	goExe := "go"
	if p, err := exec.LookPath("go"); err == nil {
		goExe = p
	}

	cmd := exec.Command(goExe, "build", "-o", binPath, srcPath)
	cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build exit agent: %v\n%s", err, out)
	}

	c := NewSubprocessClient(binPath, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	ch, err := c.Send(ctx, "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}

	var gotError bool
	for evt := range ch {
		if ae, ok := evt.(event.AgentErrorEvent); ok {
			gotError = true
			if ae.Content == "" {
				t.Error("expected non-empty error content")
			}
		}
	}

	if !gotError {
		t.Error("expected an AgentErrorEvent for process exit with code 1")
	}
}

// Verify the interface is satisfied at compile time.
var _ AgentClient = (*SubprocessClient)(nil)

// buildAgentFrom compiles src into dir and returns the binary path.
func buildAgentFrom(t *testing.T, dir, name, src string) string {
	t.Helper()

	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", dir, err)
	}
	srcPath := filepath.Join(dir, name+".go")
	if err := os.WriteFile(srcPath, []byte(src), 0644); err != nil {
		t.Fatalf("write %s: %v", srcPath, err)
	}

	binName := name
	if runtime.GOOS == "windows" {
		binName += ".exe"
	}
	binPath := filepath.Join(dir, binName)

	goExe := "go"
	if p, err := exec.LookPath("go"); err == nil {
		goExe = p
	}
	cmd := exec.Command(goExe, "build", "-o", binPath, srcPath)
	cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build %s: %v\n%s", name, err, out)
	}
	return binPath
}

const echoAgentSrc = `package main

import (
	"bufio"
	"fmt"
	"os"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		fmt.Println("{\"type\":\"answer\",\"content\":\"ok\",\"steps\":1,\"tools_used\":0}")
	}
}
`

// A path containing a space used to be re-split on whitespace, so the binary
// could never be found.
func TestSubprocessClient_BinaryPathWithSpaces(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "My Agents")
	bin := buildAgentFrom(t, dir, "spaced_agent", echoAgentSrc)
	if !strings.Contains(bin, " ") {
		t.Fatalf("test setup: expected a space in %q", bin)
	}

	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	ch, err := c.Send(ctx, "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	var got []interface{}
	for evt := range ch {
		got = append(got, evt)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 event, got %d: %+v", len(got), got)
	}
	if _, ok := got[0].(event.AnswerEvent); !ok {
		t.Fatalf("expected AnswerEvent, got %T", got[0])
	}
}

const slowAgentSrc = `package main

import (
	"bufio"
	"fmt"
	"os"
	"time"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		fmt.Println("{\"type\":\"step\",\"step\":1,\"total\":2,\"status\":\"running\"}")
		time.Sleep(30 * time.Second)
		fmt.Println("{\"type\":\"answer\",\"content\":\"late\",\"steps\":1,\"tools_used\":0}")
	}
}
`

// Cancelling a turn must stop the child. Leaving it alive lets the tail of this
// turn's output surface as the NEXT turn's events.
func TestSubprocessClient_CancelKillsChild(t *testing.T) {
	bin := buildAgentFrom(t, t.TempDir(), "slow_agent", slowAgentSrc)

	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithCancel(context.Background())
	ch, err := c.Send(ctx, "start")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	if _, ok := <-ch; !ok {
		t.Fatal("expected the first event before cancelling")
	}

	c.mu.Lock()
	pid := c.proc.cmd.Process.Pid
	c.mu.Unlock()

	cancel()
	for range ch { // drain
	}

	deadline := time.Now().Add(5 * time.Second)
	for {
		c.mu.Lock()
		started := c.started
		c.mu.Unlock()
		if !started {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("client still holds a started process 5s after cancellation")
		}
		time.Sleep(20 * time.Millisecond)
	}

	if daemon.PIDAlive(pid) {
		t.Errorf("child pid %d is still alive after cancellation", pid)
	}
}

const garbageAgentSrc = `package main

import (
	"bufio"
	"fmt"
	"os"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		fmt.Println("this is not json at all")
		fmt.Println("{\"type\":\"brand_new_type\"}")
		fmt.Println("{\"type\":\"answer\",\"content\":\"done\",\"steps\":1,\"tools_used\":0}")
	}
}
`

// An unreadable line must be surfaced, not silently dropped, and must not end
// the turn.
func TestSubprocessClient_UnparseableLineIsVisible(t *testing.T) {
	bin := buildAgentFrom(t, t.TempDir(), "garbage_agent", garbageAgentSrc)

	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	ch, err := c.Send(ctx, "hello")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}

	var warnings int
	var answered bool
	for evt := range ch {
		switch e := evt.(type) {
		case event.StatusEvent:
			if e.Status == "warning" {
				warnings++
			}
		case event.AnswerEvent:
			answered = true
		}
	}
	if warnings != 2 {
		t.Errorf("expected 2 warning status events (bad JSON + unknown type), got %d", warnings)
	}
	if !answered {
		t.Error("the turn must still reach its answer after an unreadable line")
	}
}

// A cancelled turn kills the child on purpose, so the resulting non-zero exit /
// closed pipe must NOT surface as an agent error — that put a spurious
// "exited with code -1" bubble under the "cancelled" line about half the time.
func TestSubprocessClient_CancelEmitsNoSpuriousError(t *testing.T) {
	bin := buildAgentFrom(t, t.TempDir(), "slow_cancel_agent", slowAgentSrc)

	const runs = 8
	for i := 0; i < runs; i++ {
		c := NewSubprocessClient(bin, nil, false)
		ctx, cancel := context.WithCancel(context.Background())

		ch, err := c.Send(ctx, "start")
		if err != nil {
			t.Fatalf("run %d: Send: %v", i, err)
		}
		if _, ok := <-ch; !ok {
			t.Fatalf("run %d: expected an event before cancelling", i)
		}
		cancel()

		for evt := range ch {
			if ae, ok := evt.(event.AgentErrorEvent); ok {
				t.Fatalf("run %d: cancelling emitted a spurious agent error: %q", i, ae.Content)
			}
		}
		c.Close()
	}
}

// The turn after a cancel must respawn cleanly: the cancel path clears the
// client's pipes from another goroutine, so an unsynchronised read of them in
// Send was both a data race and a nil-deref.
func TestSubprocessClient_SendAfterCancelRespawns(t *testing.T) {
	bin := buildAgentFrom(t, t.TempDir(), "respawn_agent", echoAgentSrc)

	c := NewSubprocessClient(bin, nil, false)
	defer c.Close()

	for i := 0; i < 10; i++ {
		ctx, cancel := context.WithCancel(context.Background())
		ch, err := c.Send(ctx, "cancel me")
		if err != nil {
			t.Fatalf("iteration %d: Send: %v", i, err)
		}
		cancel()
		for range ch {
		}

		// A full turn immediately afterwards, while the cancel teardown may
		// still be running.
		ch2, err := c.Send(context.Background(), "real turn")
		if err != nil {
			t.Fatalf("iteration %d: Send after cancel: %v", i, err)
		}
		var answered bool
		for evt := range ch2 {
			if _, ok := evt.(event.AnswerEvent); ok {
				answered = true
			}
		}
		if !answered {
			t.Fatalf("iteration %d: the turn after a cancel produced no answer", i)
		}
	}
}

// Killing the agent mid-turn produced "agent process exited with code
// 4294967295". That is 0xFFFFFFFF — Windows' force-terminated status, not a
// code the agent chose — and it reads as memory corruption rather than "it was
// stopped". The transport respawns on the next Send, so the message must also
// say that recovery is automatic; nothing else on screen tells the user.
func TestDescribeAgentExitExplainsATerminatedProcess(t *testing.T) {
	for _, code := range []int{windowsTerminated, -1} {
		got := describeAgentExit(code)
		if strings.Contains(got, "4294967295") {
			t.Errorf("code %d leaked the raw status: %q", code, got)
		}
		if !strings.Contains(got, "stopped") {
			t.Errorf("code %d does not say it was stopped: %q", code, got)
		}
		if !strings.Contains(got, "next message") {
			t.Errorf("code %d does not say recovery is automatic: %q", code, got)
		}
	}
}

// A real non-zero exit still reports its code — that one is a genuine signal.
func TestDescribeAgentExitKeepsARealExitCode(t *testing.T) {
	got := describeAgentExit(2)

	if !strings.Contains(got, "code 2") {
		t.Errorf("a real exit code should survive: %q", got)
	}
	if !strings.Contains(got, "next message") {
		t.Errorf("should still say recovery is automatic: %q", got)
	}
}
