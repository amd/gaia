// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ui

import (
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/ui/preflight"
)

// A --query run used to ignore the model the chat remembers: a user on the AMD
// gateway got the local default loaded (or a multi-GB download demanded) for a
// one-line question.
func TestOneShotModel(t *testing.T) {
	saved := func(model string) func() (preflight.LastModel, error) {
		return func() (preflight.LastModel, error) {
			return preflight.LastModel{Provider: "amd", Model: model}, nil
		}
	}
	unread := func() (preflight.LastModel, error) {
		t.Fatal("the saved model must not be read when the launch already decided")
		return preflight.LastModel{}, nil
	}

	t.Run("a remembered gateway model is used", func(t *testing.T) {
		got, notice := oneShotModel("gaia", "", false, saved("amd.DeepSeek-V4.1-Flash"))
		if got != "amd.DeepSeek-V4.1-Flash" || notice != "" {
			t.Fatalf("got %q, notice %q", got, notice)
		}
	})

	t.Run("a remembered local model is used", func(t *testing.T) {
		got, _ := oneShotModel("gaia", "", false, saved("Gemma-4-E4B-it-GGUF"))
		if got != "Gemma-4-E4B-it-GGUF" {
			t.Fatalf("got %q", got)
		}
	})

	t.Run("an explicit --model wins", func(t *testing.T) {
		if got, _ := oneShotModel("gaia", "amd.Gemma-4-31B", false, unread); got != "amd.Gemma-4-31B" {
			t.Fatalf("got %q", got)
		}
	})

	t.Run("--use-claude is not overridden", func(t *testing.T) {
		if got, _ := oneShotModel("gaia", "", true, unread); got != "" {
			t.Fatalf("got %q", got)
		}
	})

	t.Run("another agent keeps its own model", func(t *testing.T) {
		if got, _ := oneShotModel("email", "", false, unread); got != "" {
			t.Fatalf("got %q", got)
		}
	})

	t.Run("nothing saved runs the default quietly", func(t *testing.T) {
		got, notice := oneShotModel("gaia", "", false, saved(""))
		if got != "" || notice != "" {
			t.Fatalf("got %q, notice %q", got, notice)
		}
	})

	t.Run("a saved Claude model says how to use it", func(t *testing.T) {
		got, notice := oneShotModel("gaia", "", false, saved("claude-opus-5"))
		if got != "" || !strings.Contains(notice, "--use-claude --claude-model claude-opus-5") {
			t.Fatalf("got %q, notice %q", got, notice)
		}
	})

	t.Run("an unreadable config is reported", func(t *testing.T) {
		got, notice := oneShotModel("gaia", "", false, func() (preflight.LastModel, error) {
			return preflight.LastModel{}, errors.New("config.json cannot be read")
		})
		if got != "" || !strings.Contains(notice, "config.json cannot be read") {
			t.Fatalf("got %q, notice %q", got, notice)
		}
	})
}

// A --query turn on a cloud model must not be refused for a local model it
// never loads.
func TestOneShotReadinessChecksTheChosenModel(t *testing.T) {
	if got := oneShotConfig("gaia", "GAIA", "fireworks.deepseek-v4p1-flash").Model; got != "fireworks.deepseek-v4p1-flash" {
		t.Fatalf("readiness checks model %q, want the one the turn runs on", got)
	}
	if got := oneShotConfig("gaia", "GAIA", "").Model; got != "" {
		t.Fatalf("with no model chosen, readiness checks %q, want the agent default", got)
	}
	// The email agent ignores --model, so its own local model is still checked.
	if got := oneShotConfig("email", "Email", "fireworks.deepseek-v4p1-flash").Model; got != "" {
		t.Fatalf("email readiness checks %q, want its own default", got)
	}
}
