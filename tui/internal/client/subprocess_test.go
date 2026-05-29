package client

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"

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

	c := NewSubprocessClient(bin, true)
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

	c := NewSubprocessClient(bin, false)
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
	c := NewSubprocessClient("nonexistent_binary_xyz_12345", false)
	defer c.Close()

	ctx := context.Background()
	_, err := c.Send(ctx, "hello")
	if err == nil {
		t.Fatal("expected error for invalid command, got nil")
	}
}

func TestSubprocessClient_EmptyCommand(t *testing.T) {
	c := NewSubprocessClient("", false)

	ctx := context.Background()
	_, err := c.Send(ctx, "hello")
	if err == nil {
		t.Fatal("expected error for empty command, got nil")
	}
}

func TestSubprocessClient_CloseBeforeSend(t *testing.T) {
	c := NewSubprocessClient("echo", false)

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

	c := NewSubprocessClient(binPath, false)
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
