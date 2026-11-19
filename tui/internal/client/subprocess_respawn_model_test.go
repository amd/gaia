// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"context"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/event"
)

// argvAgentSrc answers each query with its own argv, and exits 1 on "die" —
// a crash mid-session, which is what triggers a respawn.
const argvAgentSrc = `package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		if strings.Contains(scanner.Text(), "die") {
			fmt.Fprintln(os.Stderr, "Loading model: Gemma-4-E4B-it-GGUF...")
			os.Exit(1)
		}
		line, _ := json.Marshal(map[string]string{"type": "final", "answer": strings.Join(os.Args[1:], " ")})
		fmt.Println(string(line))
	}
}
`

func turn(t *testing.T, c *SubprocessClient, query string) (answer, agentErr string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	ch, err := c.Send(ctx, query)
	if err != nil {
		t.Fatalf("Send %q: %v", query, err)
	}
	for evt := range ch {
		switch e := evt.(type) {
		case event.CanonicalFinalEvent:
			answer = e.Answer
		case event.AgentErrorEvent:
			agentErr = e.Content
		}
	}
	return answer, agentErr
}

// The reported bug: `/model fireworks.…` then a crash, and the next message
// came back on the launch model — a cloud conversation silently went local.
func TestConfirmedModelSwitchSurvivesARespawn(t *testing.T) {
	isolateSubprocessConnection(t)
	old := subprocessLemonadePorts
	subprocessLemonadePorts = nil
	t.Cleanup(func() { subprocessLemonadePorts = old })

	bin := buildAgentFrom(t, t.TempDir(), "argv_agent", argvAgentSrc)
	c := NewCanonicalSubprocessClient(bin, []string{"--json-events", "--model", "Qwen3.6-35B-A3B-GGUF"}, false)
	defer c.Close()

	if got, _ := turn(t, c, "hi"); got != "--json-events --model Qwen3.6-35B-A3B-GGUF" {
		t.Fatalf("launch argv: %q", got)
	}
	if !c.RecordModelSwitch("fireworks.deepseek-v4p1-flash", false) {
		t.Fatal("a confirmed switch on a running child was not recorded")
	}
	_, crash := turn(t, c, "die")
	if !strings.Contains(crash, "exited unexpectedly") {
		t.Fatalf("expected the crash to be reported, got %q", crash)
	}
	// The dead process's stderr is labelled, so its model-load lines are not
	// read as what the restart did.
	if !strings.Contains(crash, exitedOutputLabel+"\nLoading model: Gemma-4-E4B-it-GGUF") {
		t.Fatalf("stderr of the exited process is not labelled: %q", crash)
	}
	if got, _ := turn(t, c, "2+2"); got != "--json-events --model fireworks.deepseek-v4p1-flash" {
		t.Fatalf("respawned child reverted to the launch model: %q", got)
	}
}

func TestRecordModelSwitchBuildsTheFlagsForEachBackend(t *testing.T) {
	launch := []string{"--json-events", "--full-access", "--model", "Qwen3.6-35B-A3B-GGUF"}
	c := NewCanonicalSubprocessClient("unused", append([]string(nil), launch...), false)

	// Lemonade -> Claude: the agent keeps its Lemonade model and adds Claude.
	c.RecordModelSwitch("claude-opus-5", true)
	want := []string{"--json-events", "--full-access", "--model", "Qwen3.6-35B-A3B-GGUF", "--use-claude", "--claude-model", "claude-opus-5"}
	if !reflect.DeepEqual(c.args, want) {
		t.Fatalf("Lemonade -> Claude argv: %v", c.args)
	}
	if !c.ClaudeAtLaunch() || c.ClaudeModelAtLaunch() != "claude-opus-5" {
		t.Fatal("Claude getters disagree with the recorded switch")
	}

	// Claude -> Claude replaces the Claude model, never duplicates the pair.
	c.RecordModelSwitch("claude-sonnet-5", true)
	want = []string{"--json-events", "--full-access", "--model", "Qwen3.6-35B-A3B-GGUF", "--use-claude", "--claude-model", "claude-sonnet-5"}
	if !reflect.DeepEqual(c.args, want) {
		t.Fatalf("Claude -> Claude argv: %v", c.args)
	}

	// Claude -> Lemonade cloud: the Claude pair must go, or the respawn
	// would send the conversation back to Anthropic.
	c.RecordModelSwitch("fireworks.deepseek-v4p1-flash", false)
	want = []string{"--json-events", "--full-access", "--model", "fireworks.deepseek-v4p1-flash"}
	if !reflect.DeepEqual(c.args, want) {
		t.Fatalf("Claude -> Lemonade argv: %v", c.args)
	}
	if c.ClaudeAtLaunch() || c.ModelAtLaunch() != "fireworks.deepseek-v4p1-flash" {
		t.Fatal("Lemonade getters disagree with the recorded switch")
	}

	// The = form a caller may have launched with is replaced too.
	c = NewCanonicalSubprocessClient("unused", []string{"--use-claude=true", "--claude-model=claude-opus-5", "--model=old"}, false)
	c.RecordModelSwitch("Gemma-4-E4B-it-GGUF", false)
	if want := []string{"--model", "Gemma-4-E4B-it-GGUF"}; !reflect.DeepEqual(c.args, want) {
		t.Fatalf("= form argv: %v", c.args)
	}
}

func TestRecordModelSwitchIgnoresLegacyAgentsAndEmptyIds(t *testing.T) {
	legacy := NewSubprocessClient("unused", []string{"--model", "existing"}, false)
	if legacy.RecordModelSwitch("fireworks.deepseek-v4p1-flash", false) || legacy.ModelAtLaunch() != "existing" {
		t.Fatal("a legacy agent has no /model; its argv must not change")
	}
	c := NewCanonicalSubprocessClient("unused", []string{"--model", "existing"}, false)
	if c.RecordModelSwitch("  ", false) || c.ModelAtLaunch() != "existing" {
		t.Fatal("an empty id must not change the respawn model")
	}
}
