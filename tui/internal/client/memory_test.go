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

	"github.com/amd/gaia/tui/internal/event"
)

// buildCanonicalMemoryMockAgent compiles a tiny agent that answers the
// memory-dump sentinel with a canonical `final` event carrying a fixed
// MemoryDump payload (JSON-encoded properly, not hand-escaped, so the test
// exercises the same marshal/unmarshal round trip the real agent does), and
// echoes anything else back as a plain final answer — so a test can also
// prove a REAL query never takes the memory branch.
func buildCanonicalMemoryMockAgent(t *testing.T) string {
	t.Helper()

	src := `package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
)

const memoryDumpQuery = "\x00gaia:memory_dump\x00"

type event struct {
	Type   string ` + "`json:\"type\"`" + `
	Answer string ` + "`json:\"answer\"`" + `
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		query := scanner.Text()
		if query == memoryDumpQuery {
			answer, _ := json.Marshal(map[string]interface{}{
				"available": true,
				"stats": map[string]interface{}{
					"total_knowledge": 2,
					"by_category":     map[string]int{"fact": 1, "preference": 1},
					"by_context":      map[string]int{"global": 2},
					"sensitive_count": 0,
					"entity_count":    0,
					"avg_confidence":  0.6,
				},
				"contexts": []map[string]interface{}{{"context": "global", "count": 2}},
				"shown":    2,
				"total":    2,
				"items": []map[string]interface{}{{
					"id": "1", "category": "fact", "content": "likes go",
					"context": "global", "confidence": 0.6, "sensitive": false,
				}},
			})
			line, _ := json.Marshal(event{Type: "final", Answer: string(answer)})
			fmt.Println(string(line))
			continue
		}
		line, _ := json.Marshal(event{Type: "final", Answer: "echo: " + query})
		fmt.Println(string(line))
	}
}
`
	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "mock_memory_agent.go")
	if err := os.WriteFile(srcPath, []byte(src), 0644); err != nil {
		t.Fatalf("write mock agent source: %v", err)
	}

	binName := "mock_memory_agent"
	if runtime.GOOS == "windows" {
		binName = "mock_memory_agent.exe"
	}
	binPath := filepath.Join(tmpDir, binName)

	goExe := "go"
	if p, err := exec.LookPath("go"); err == nil {
		goExe = p
	}

	cmd := exec.Command(goExe, "build", "-o", binPath, srcPath)
	cmd.Env = append(os.Environ(), "CGO_ENABLED=0")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build mock memory agent: %v\n%s", err, out)
	}

	return binPath
}

func TestFetchMemory_ReturnsParsedDump(t *testing.T) {
	bin := buildCanonicalMemoryMockAgent(t)

	c := NewCanonicalSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	dump, err := c.FetchMemory(ctx)
	if err != nil {
		t.Fatalf("FetchMemory: %v", err)
	}
	if !dump.Available {
		t.Fatalf("expected Available=true, got %+v", dump)
	}
	if dump.Stats.TotalKnowledge != 2 {
		t.Errorf("expected TotalKnowledge=2, got %d", dump.Stats.TotalKnowledge)
	}
	if len(dump.Items) != 1 || dump.Items[0].Content != "likes go" {
		t.Errorf("unexpected items: %+v", dump.Items)
	}
	if dump.Total != 2 || dump.Shown != 2 {
		t.Errorf("expected Total=2 Shown=2, got Total=%d Shown=%d", dump.Total, dump.Shown)
	}
}

// TestFetchMemory_SentinelNeverLeaksAsALiteralQuestion proves a REAL query
// takes the mock's "echo: " branch, not the memory branch — so a future
// refactor can't accidentally route every query through the memory path
// without a test noticing.
func TestFetchMemory_SentinelNeverLeaksAsALiteralQuestion(t *testing.T) {
	bin := buildCanonicalMemoryMockAgent(t)

	c := NewCanonicalSubprocessClient(bin, nil, false)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	ch, err := c.Send(ctx, "what do you remember about me?")
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
	var sawEcho bool
	for evt := range ch {
		if f, ok := evt.(event.CanonicalFinalEvent); ok && strings.HasPrefix(f.Answer, "echo: ") {
			sawEcho = true
		}
	}
	if !sawEcho {
		t.Fatal("a real question must be answered as a real question, not the memory dump")
	}
}
