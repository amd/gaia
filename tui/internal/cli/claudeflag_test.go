// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"strings"
	"testing"
)

// --use-claude sends the conversation to Anthropic instead of running it
// locally — a real privacy change, so the flag must exist, must be visible in
// --help, and its help text must say where the data goes.
func TestUseClaudeFlagIsRegisteredAndHonest(t *testing.T) {
	flags := rootCmd.PersistentFlags()

	f := flags.Lookup("use-claude")
	if f == nil {
		t.Fatal("--use-claude is not registered")
	}
	if f.Hidden {
		t.Error("--use-claude is hidden; a backend switch this consequential belongs in --help")
	}
	for _, must := range []string{"Anthropic", "ANTHROPIC_API_KEY"} {
		if !strings.Contains(f.Usage, must) {
			t.Errorf("--use-claude help text does not mention %q: %q", must, f.Usage)
		}
	}

	useClaude = false
	t.Cleanup(func() { useClaude = false })
	if err := flags.Set("use-claude", "true"); err != nil {
		t.Fatalf("--use-claude: %v", err)
	}
	if !useClaude {
		t.Error("--use-claude did not turn on Claude mode")
	}
}

func TestClaudeModelFlagDefaultsToSonnet5(t *testing.T) {
	flags := rootCmd.PersistentFlags()

	f := flags.Lookup("claude-model")
	if f == nil {
		t.Fatal("--claude-model is not registered")
	}
	if f.DefValue != "claude-sonnet-5" {
		t.Errorf("--claude-model default = %q, want claude-sonnet-5", f.DefValue)
	}

	t.Cleanup(func() {
		claudeModel = defaultClaudeModel
		f.Changed = false
	})
	if err := flags.Set("claude-model", "claude-opus-5"); err != nil {
		t.Fatalf("--claude-model: %v", err)
	}
	if claudeModel != "claude-opus-5" {
		t.Errorf("claudeModel = %q, want the id that was set", claudeModel)
	}
}

// The default model must only reach the child alongside --use-claude — a
// local launch forwards no Claude model at all.
func TestClaudeModelIsOnlyForwardedWithUseClaude(t *testing.T) {
	useClaude = false
	claudeModel = defaultClaudeModel
	t.Cleanup(func() { useClaude = false })

	if got := claudeModelArg(); got != "" {
		t.Errorf("a local launch forwards Claude model %q; it must forward none", got)
	}
	useClaude = true
	if got := claudeModelArg(); got != defaultClaudeModel {
		t.Errorf("claudeModelArg() = %q, want the Sonnet 5 default", got)
	}
}

// An EXPLICIT --claude-model without --use-claude would be accepted and then
// change nothing; it must be refused before any UI opens, not silently
// ignored. The un-passed default must not trip the same refusal.
func TestClaudeModelWithoutUseClaudeIsRefusedAtLaunch(t *testing.T) {
	flags := rootCmd.PersistentFlags()
	f := flags.Lookup("claude-model")
	if f == nil {
		t.Fatal("--claude-model is not registered")
	}

	useClaude = false
	t.Cleanup(func() {
		useClaude = false
		claudeModel = defaultClaudeModel
		f.Changed = false
	})

	if rootCmd.PersistentPreRunE == nil {
		t.Fatal("no PersistentPreRunE guards the flag combination")
	}
	// Default, not explicitly passed: every plain local launch goes through
	// here and must not be refused.
	if err := rootCmd.PersistentPreRunE(rootCmd, nil); err != nil {
		t.Fatalf("a plain launch with the default model was refused: %v", err)
	}

	if err := flags.Set("claude-model", "claude-opus-5"); err != nil {
		t.Fatalf("--claude-model: %v", err)
	}
	err := rootCmd.PersistentPreRunE(rootCmd, nil)
	if err == nil {
		t.Fatal("--claude-model without --use-claude was accepted")
	}
	if !strings.Contains(err.Error(), "--use-claude") {
		t.Errorf("the refusal does not name the missing flag: %v", err)
	}

	useClaude = true
	if err := rootCmd.PersistentPreRunE(rootCmd, nil); err != nil {
		t.Errorf("the pair together must be accepted: %v", err)
	}
}
