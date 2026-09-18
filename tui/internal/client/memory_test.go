package client

import (
	"context"
	"errors"
	"net/http"
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
	"strings"
)

const memoryDumpQuery = "\x00gaia:memory_dump\x00"

type event struct {
	Type   string ` + "`json:\"type\"`" + `
	Answer string ` + "`json:\"answer\"`" + `
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		// Mirror the real agent: a query arrives wrapped so its newlines
		// survive, and a bare line is still accepted (gaia_agent.stdio.parse_query).
		query := scanner.Text()
		if strings.HasPrefix(query, "{") {
			var wrapper map[string]string
			if json.Unmarshal([]byte(query), &wrapper) == nil {
				if q, ok := wrapper["gaia_query"]; ok {
					query = q
				}
			}
		}
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

// ---------------------------------------------------------------------------
// SSEClient.FetchMemory (#3978) -- the daemon-relayed side of MemoryProvider.
// ---------------------------------------------------------------------------

func TestSSEFetchMemoryReturnsParsedDump(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.memoryBody = `{"available":true,"stats":{"total_knowledge":2,"by_category":{"fact":1,"preference":1},"by_context":{"global":2},"sensitive_count":0,"entity_count":0,"avg_confidence":0.6},"contexts":[{"context":"global","count":2}],"shown":2,"total":2,"items":[{"id":"1","category":"fact","content":"likes go","context":"global","confidence":0.6,"sensitive":false}]}`
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
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
}

// A peer below 2.13 has no /memory route at all -- trusting whatever a stray
// 404 handler answers would be exactly the "confident empty dump" failure
// mode #3978 exists to avoid, so the client must refuse before calling it.
func TestSSEFetchMemoryRefusesAnOldPeerContract(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.12" // predates memory (2.13)
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := c.FetchMemory(ctx)
	if err == nil {
		t.Fatal("expected ErrMemoryContractTooOld for a peer below 2.13, got nil")
	}
	var tooOld *ErrMemoryContractTooOld
	if !errors.As(err, &tooOld) {
		t.Fatalf("error = %v (%T), want *ErrMemoryContractTooOld", err, err)
	}
	if tooOld.Version != "2.12" {
		t.Errorf("tooOld.Version = %q, want 2.12", tooOld.Version)
	}
}

func TestSSEFetchMemoryAcceptsExactFloorVersion(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.FetchMemory(ctx); err != nil {
		t.Fatalf("a peer at exactly the floor version must be accepted: %v", err)
	}
}

// A non-200 must surface the daemon's detail text, never a silently empty
// dump that reads as "the agent remembers nothing" (CLAUDE.md: no silent
// fallbacks).
func TestSSEFetchMemorySurfacesNonOKDetail(t *testing.T) {
	f := newFakeRelay(t)
	f.contractVersion = "2.13"
	f.memoryStatus = http.StatusServiceUnavailable
	c := f.client(t)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	dump, err := c.FetchMemory(ctx)
	if err == nil {
		t.Fatalf("expected an error for a 503, got a dump: %+v", dump)
	}
	if !strings.Contains(err.Error(), "memory store unavailable") {
		t.Errorf("error does not surface the daemon's detail: %v", err)
	}
	var tooOld *ErrMemoryContractTooOld
	if errors.As(err, &tooOld) {
		t.Fatalf("a 503 must surface as a plain error, not the contract-too-old gate: %v", err)
	}
}
