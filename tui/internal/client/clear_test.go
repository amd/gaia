package client

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

func TestClearWireHelper(t *testing.T) {
	mode := os.Getenv("GAIA_TEST_CLEAR_HELPER")
	if mode == "" {
		return
	}
	history := []string{}
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		var wrapper map[string]string
		if err := json.Unmarshal(scanner.Bytes(), &wrapper); err != nil {
			os.Exit(2)
		}
		query := wrapper[queryKey]
		answer := ""
		if query == clearConversationQuery {
			switch mode {
			case "error":
				fmt.Println(`{"type":"error","detail":"reset refused"}`)
				continue
			case "old":
				answer = "unrecognized"
			default:
				history = nil
				answer = "conversation_cleared"
			}
		} else {
			answer = strings.Join(history, ",")
			history = append(history, query)
		}
		line, _ := json.Marshal(map[string]string{"type": "final", "answer": answer})
		fmt.Println(string(line))
	}
	os.Exit(0)
}

func TestClearConversationAcrossSubprocessTurns(t *testing.T) {
	t.Setenv("GAIA_TEST_CLEAR_HELPER", "ok")
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	c := NewCanonicalSubprocessClient(self, []string{"-test.run=^TestClearWireHelper$"}, false)
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.ClearConversation(ctx); err != nil {
		t.Fatal(err)
	}
	if c.started {
		t.Fatal("clearing an unused conversation spawned a child")
	}
	turn := func(q string) string {
		ch, err := c.Send(ctx, q)
		if err != nil {
			t.Fatal(err)
		}
		answer := "missing terminal"
		for evt := range ch {
			if final, ok := evt.(event.CanonicalFinalEvent); ok {
				answer = final.Answer
			}
		}
		return answer
	}
	turn("first")
	if got := turn("followup"); got != "first" {
		t.Fatalf("history did not accumulate: %q", got)
	}
	proc := c.proc
	for i := 0; i < 2; i++ {
		if err := c.ClearConversation(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if got := turn("fresh"); got != "" {
		t.Fatalf("cleared history leaked: %q", got)
	}
	if proc != c.proc {
		t.Fatal("clear restarted the agent")
	}
}

func TestClearConversationRequiresAcknowledgment(t *testing.T) {
	for _, mode := range []string{"error", "old"} {
		t.Run(mode, func(t *testing.T) {
			t.Setenv("GAIA_TEST_CLEAR_HELPER", mode)
			self, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			c := NewCanonicalSubprocessClient(self, []string{"-test.run=^TestClearWireHelper$"}, false)
			defer c.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			ch, err := c.Send(ctx, "hello")
			if err != nil {
				t.Fatal(err)
			}
			for range ch {
			}
			if err := c.ClearConversation(ctx); err == nil {
				t.Fatal("unacknowledged reset was accepted")
			}
		})
	}
}
