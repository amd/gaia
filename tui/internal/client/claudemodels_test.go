// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package client

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
)

// Haiku 4.5 is the id the fleet actually runs on, and the one people get
// wrong — so it is asserted by name rather than left to a table.
func TestHaiku45IsAcceptedByItsExactID(t *testing.T) {
	if err := ValidateClaudeModel("claude-haiku-4-5"); err != nil {
		t.Fatalf("claude-haiku-4-5 must be accepted: %v", err)
	}
	if err := ValidateClaudeModel("claude-haiku-4-5-20250101"); err == nil {
		t.Error("a date-suffixed id must be refused — no such model id exists")
	}
}

func TestUnknownClaudeModelIsRefusedWithTheAcceptedList(t *testing.T) {
	err := ValidateClaudeModel("claude-haiku-45")
	if err == nil {
		t.Fatal("a typo'd model id must be refused, not passed through to Anthropic")
	}
	// The refusal is the ONLY place the user finds out what to type instead;
	// without the list it is no more useful than the 404 it prevents.
	for _, id := range ClaudeModelIDs() {
		if !strings.Contains(err.Error(), id) {
			t.Errorf("refusal does not offer %q: %v", id, err)
		}
	}
}

// "" is a documented choice — let the agent pick — not a missing value.
func TestEmptyClaudeModelIsAllowed(t *testing.T) {
	if err := ValidateClaudeModel(""); err != nil {
		t.Errorf(`--claude-model "" must be allowed: %v`, err)
	}
}

func TestClaudeModelShortName(t *testing.T) {
	for _, tc := range []struct{ id, want string }{
		{"claude-haiku-4-5", "haiku-4.5"},
		{"claude-sonnet-5", "sonnet-5"},
		{"claude-opus-5", "opus-5"},
		{"claude-fable-5", "fable-5"},
		// Unknown to this build: still rendered, never dropped — the agent
		// can be newer than the TUI and its ping is authoritative.
		{"claude-newmodel-9-1", "newmodel-9.1"},
		// Not a Claude id at all: passed through unchanged rather than
		// mangled into something that looks like one.
		{"Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF"},
	} {
		if got := ClaudeModelShortName(tc.id); got != tc.want {
			t.Errorf("ClaudeModelShortName(%q) = %q, want %q", tc.id, got, tc.want)
		}
	}
}

// The Go list and the agent's own CLAUDE_MODELS must not drift: an id the
// launch flag accepts but `/model` refuses (or the reverse) is a bug the user
// experiences as "it worked yesterday". Parsed out of the Python source the
// same way palette_test parses submit — the source file IS the contract.
func TestGoAndPythonClaudeModelListsAgree(t *testing.T) {
	src, err := os.ReadFile(pythonStdioPath(t))
	if err != nil {
		t.Skipf("agent source not available in this checkout: %v", err)
	}
	block := regexp.MustCompile(`(?s)CLAUDE_MODELS: Dict\[str, str\] = \{(.*?)\}`).
		FindSubmatch(src)
	if block == nil {
		t.Fatal("CLAUDE_MODELS not found in gaia_agent/stdio.py — has it been renamed?")
	}
	var pyIDs []string
	for _, m := range regexp.MustCompile(`"(claude-[^"]+)"\s*:`).FindAllSubmatch(block[1], -1) {
		pyIDs = append(pyIDs, string(m[1]))
	}
	if strings.Join(pyIDs, ",") != strings.Join(ClaudeModelIDs(), ",") {
		t.Errorf("Claude model lists disagree:\n  Go:     %v\n  Python: %v",
			ClaudeModelIDs(), pyIDs)
	}
}

// pythonStdioPath locates the agent module relative to THIS source file, so
// the test does not depend on the working directory the suite is run from.
func pythonStdioPath(t *testing.T) string {
	t.Helper()
	_, self, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate this test's own source file")
	}
	repo := filepath.Join(filepath.Dir(self), "..", "..", "..")
	return filepath.Join(repo, "hub", "agents", "gaia", "python", "gaia_agent", "stdio.py")
}

// Every refusal and every help string names ExampleClaudeModelID. If it ever
// stops being one of the ids we accept, those messages start advertising an id
// ValidateClaudeModel rejects — the exact confusion they exist to prevent.
func TestTheExampleModelIsOneWeAccept(t *testing.T) {
	if err := ValidateClaudeModel(ExampleClaudeModelID); err != nil {
		t.Errorf("ExampleClaudeModelID (%q) is advertised but refused: %v",
			ExampleClaudeModelID, err)
	}
}
