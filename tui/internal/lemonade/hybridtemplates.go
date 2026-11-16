// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/amd/gaia/tui/internal/filelock"
)

// Model ids the built-in hybrid templates name. They mirror
// src/gaia/llm/hybrid_templates.py; TestHybridTemplatesMatchPython pins them.
const (
	HybridSonnetModel        = "amd.Claude-Sonnet-5-5"
	HybridFireworksCheap     = "fireworks.deepseek-v4p1-flash"
	HybridClaudeDirectCheap  = "claude-haiku-4-5"
	HybridClaudeDirectStrong = "claude-opus-5"
	HybridSmallLocalModel    = "Gemma-4-E4B-it-GGUF"
	HybridLargeLocalModel    = "Qwen3.6-35B-A3B-GGUF"
	// HybridMeasuredTemplate is the one pair the shipped policies were tuned on.
	HybridMeasuredTemplate = "gateway-deepseek-opus"
)

// Hybrid model providers, in the order choices are grouped.
const (
	HybridFromLocal     = "local"
	HybridFromGateway   = "amd"
	HybridFromFireworks = "fireworks"
	HybridFromClaude    = "claude"
)

// HybridProviders are the providers a hybrid model may come from.
var HybridProviders = []string{HybridFromLocal, HybridFromGateway, HybridFromFireworks, HybridFromClaude}

// HybridProviderLabel names a provider for a group heading.
func HybridProviderLabel(p string) string {
	switch p {
	case HybridFromLocal:
		return "On this PC"
	case HybridFromGateway:
		return "AMD LLM Gateway"
	case HybridFromFireworks:
		return "Fireworks AI"
	case HybridFromClaude:
		return "Claude (Anthropic API)"
	}
	return p
}

// HybridProviderOf is which provider serves a model id.
func HybridProviderOf(id string) string {
	switch {
	case strings.HasPrefix(id, "amd."):
		return HybridFromGateway
	case strings.HasPrefix(id, "fireworks."):
		return HybridFromFireworks
	case strings.HasPrefix(id, "claude-"):
		return HybridFromClaude
	}
	return HybridFromLocal
}

// HybridCatalog is what each provider offers on this PC right now. A provider
// whose list could not be read has its error in Errs, so nothing is claimed
// absent that was never checked.
type HybridCatalog struct {
	Models map[string][]string
	Errs   map[string]error
	// ClaudeKey is set when ANTHROPIC_API_KEY reaches the agent.
	ClaudeKey bool
	LocalOnly bool
	// DefaultLocal is the local chat model GAIA uses by default.
	DefaultLocal string
}

// Find returns the provider's own spelling of id, or why it is not offered.
func (c HybridCatalog) Find(id string) (string, error) {
	p := HybridProviderOf(id)
	if err := c.Errs[p]; err != nil {
		return "", fmt.Errorf("%s couldn't be checked: %v", HybridProviderLabel(p), err)
	}
	for _, have := range c.Models[p] {
		if strings.EqualFold(have, id) || (p == HybridFromFireworks && SameCloudModel(have, id)) ||
			(p == HybridFromLocal && SameLocalID(have, id)) {
			return have, nil
		}
	}
	return "", c.missing(p, id)
}

func (c HybridCatalog) missing(p, id string) error {
	if p == HybridFromLocal {
		return fmt.Errorf("download %s first (/models)", id)
	}
	if len(c.Models[p]) > 0 {
		return fmt.Errorf("%s doesn't offer %s", HybridProviderLabel(p), id)
	}
	switch p {
	case HybridFromGateway:
		return fmt.Errorf("connect the AMD LLM Gateway first (/gateway)")
	case HybridFromFireworks:
		return fmt.Errorf("add a Fireworks key first (/provider)")
	}
	return fmt.Errorf("set ANTHROPIC_API_KEY first, then restart GAIA")
}

// HybridTemplate is a ready-made hybrid setup.
type HybridTemplate struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Cheap       string   `json:"cheap_model"`
	Strong      string   `json:"strong_model"`
	Classifier  string   `json:"classifier_model"`
	Policy      string   `json:"policy"`
	Providers   []string `json:"providers_needed"`
	Available   bool     `json:"available"`
	Reason      string   `json:"reason"`
	Measured    bool     `json:"measured"`
}

// Settings is base with the template's models and policy; a preset policy
// brings its own rules.
func (t HybridTemplate) Settings(base HybridSettings) HybridSettings {
	base.Cheap, base.Strong, base.Classifier, base.Policy = t.Cheap, t.Strong, t.Classifier, t.Policy
	base.Rules = nil
	return base
}

// BuiltinHybridTemplates are the seven templates, judged against what c offers.
func BuiltinHybridTemplates(c HybridCatalog) []HybridTemplate {
	local := c.DefaultLocal
	if local == "" {
		local = HybridSmallLocalModel
	}
	fwStrong := DefaultHybridStrongModel
	if _, err := c.Find(DefaultHybridStrongModel); err != nil && c.ClaudeKey {
		fwStrong = HybridClaudeDirectStrong
	}
	list := []HybridTemplate{
		{ID: HybridMeasuredTemplate, Name: "DeepSeek + Opus (gateway)",
			Description: "The default: DeepSeek for routine turns, Opus for hard ones.",
			Cheap:       DefaultHybridCheapModel, Strong: DefaultHybridStrongModel, Policy: HybridPolicyQuality, Measured: true},
		{ID: "gateway-deepseek-sonnet", Name: "DeepSeek + Sonnet (gateway)",
			Description: "A cheaper strong model.",
			Cheap:       DefaultHybridCheapModel, Strong: HybridSonnetModel, Policy: HybridPolicyQuality},
		{ID: "gateway-balanced", Name: "DeepSeek + Opus, balanced",
			Description: "Also sends moderate code changes to the cheap model.",
			Cheap:       DefaultHybridCheapModel, Strong: DefaultHybridStrongModel, Policy: HybridPolicyBalanced},
		{ID: "local-plus-cloud", Name: "Local + Opus",
			Description: "Easy turns on this PC, hard ones on Opus.",
			Cheap:       local, Strong: DefaultHybridStrongModel, Policy: HybridPolicyQuality},
		{ID: "all-local", Name: "All on this PC",
			Description: "Small and large local models; works with Local only on.",
			Cheap:       HybridSmallLocalModel, Strong: HybridLargeLocalModel, Policy: HybridPolicyQuality},
		{ID: "fireworks", Name: "Fireworks DeepSeek + Opus",
			Description: "DeepSeek on Fireworks for routine turns.",
			Cheap:       HybridFireworksCheap, Strong: fwStrong, Policy: HybridPolicyQuality},
		{ID: "claude-direct", Name: "Claude Haiku + Opus",
			Description: "Both on the Anthropic API.",
			Cheap:       HybridClaudeDirectCheap, Strong: HybridClaudeDirectStrong, Policy: HybridPolicyQuality},
	}
	for i := range list {
		list[i] = c.judge(list[i])
	}
	return list
}

// judge fills in the providers a template needs and whether it can be used,
// with each member in its provider's own spelling.
func (c HybridCatalog) judge(t HybridTemplate) HybridTemplate {
	t.Classifier = t.Cheap
	seen := map[string]bool{}
	cloud := false
	for _, m := range []*string{&t.Cheap, &t.Strong} {
		p := HybridProviderOf(*m)
		if !seen[p] {
			seen[p] = true
			t.Providers = append(t.Providers, p)
		}
		cloud = cloud || p != HybridFromLocal
	}
	t.Available = true
	if c.LocalOnly && cloud {
		t.Available, t.Reason = false, "Local only is on, and this sends turns off this PC"
		return t
	}
	for _, m := range []*string{&t.Cheap, &t.Strong} {
		if HybridProviderOf(*m) == HybridFromClaude {
			if !c.ClaudeKey {
				t.Available, t.Reason = false, "set ANTHROPIC_API_KEY first, then restart GAIA"
				return t
			}
			continue
		}
		have, err := c.Find(*m)
		if err != nil {
			t.Available, t.Reason = false, upperFirst(err.Error())
			return t
		}
		*m = have
	}
	t.Classifier = t.Cheap
	return t
}

func upperFirst(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

// IsMeasuredHybrid reports whether s is the pair the policies were tuned on.
func IsMeasuredHybrid(s HybridSettings) bool {
	h := s.Models()
	return h.Cheap == DefaultHybridCheapModel && h.Strong == DefaultHybridStrongModel &&
		h.Classifier == h.Cheap && s.Policy == HybridPolicyQuality
}

// DefaultLocalChatModel is resolve_default_chat_model for a local model:
// config.json's default_model when it names one on this PC, else Gemma.
func DefaultLocalChatModel() (string, error) {
	path := ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return defaultChatModel, nil
	}
	raw, err := filelock.ReadFile(path)
	if os.IsNotExist(err) {
		return defaultChatModel, nil
	}
	if err != nil {
		return "", fmt.Errorf("cannot read %s for the default model: %v", path, err)
	}
	var doc struct {
		Default *string `json:"default_model"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return "", fmt.Errorf("%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	if doc.Default == nil {
		return defaultChatModel, nil
	}
	id := strings.TrimSpace(*doc.Default)
	if id == "" || IsCloudID(id) || strings.HasPrefix(id, "claude-") {
		return defaultChatModel, nil
	}
	return id, nil
}
