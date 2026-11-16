// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"

	"github.com/amd/gaia/tui/internal/filelock"
)

// The templates, the models they name and the notes are a copy of
// src/gaia/llm/hybrid_templates.py, used when the Agent UI server isn't there
// to judge them. TestHybridTemplatesMatchPython pins the copy to the source.
const (
	HybridSonnetModel        = "amd.Claude-Sonnet-5-5"
	HybridFireworksCheap     = "fireworks.deepseek-v4p1-flash"
	HybridClaudeDirectCheap  = "claude-haiku-5-5"
	HybridClaudeDirectStrong = "claude-opus-5-5"
	HybridSmallLocalModel    = "Gemma-4-E4B-it-GGUF"
	HybridLargeLocalModel    = "Qwen3.6-35B-A3B-GGUF"
	// HybridMeasuredTemplate is the one pair the shipped policies were tuned on.
	HybridMeasuredTemplate = "gateway-deepseek-opus"

	HybridMeasuredNote = "This pairing hasn't been measured yet, so how much it saves and how well " +
		"it answers are unknown."
	HybridClassifierNote = "The classifier runs on this PC; a small local model may route less " +
		"accurately than a cloud one."
)

// HybridClaudeModels are the Anthropic models hybrid offers with an Anthropic key.
var HybridClaudeModels = []string{HybridClaudeDirectCheap, "claude-sonnet-5-5", HybridClaudeDirectStrong}

// Hybrid model providers, in the order choices are grouped.
const (
	HybridFromLocal     = "local"
	HybridFromGateway   = "amd"
	HybridFromFireworks = "fireworks"
	HybridFromClaude    = "claude"
)

// HybridProviders are the providers a hybrid model may come from.
var HybridProviders = []string{HybridFromLocal, HybridFromGateway, HybridFromFireworks, HybridFromClaude}

// hybridProviderNames are PROVIDER_NAMES, for reasons and errors.
var hybridProviderNames = map[string]string{
	HybridFromLocal:     "this PC",
	HybridFromGateway:   "the AMD LLM Gateway",
	HybridFromFireworks: "Fireworks AI",
	HybridFromClaude:    "Anthropic",
}

// hybridSetupHints are SETUP_HINTS in the TUI's words.
var hybridSetupHints = map[string]string{
	HybridFromLocal:     "Download a model with /models",
	HybridFromGateway:   "Connect the AMD LLM Gateway with /gateway",
	HybridFromFireworks: "Add a Fireworks key with /provider",
	HybridFromClaude:    "Add an Anthropic API key (ANTHROPIC_API_KEY in GAIA's environment)",
}

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
		return "Anthropic (Claude)"
	}
	return p
}

// HybridSetupHint is how to set up provider p.
func HybridSetupHint(p string) string { return hybridSetupHints[p] }

// HybridProviderOf is provider_of: which provider serves a model id.
func HybridProviderOf(id string) string {
	switch {
	case strings.HasPrefix(id, "claude-"):
		return HybridFromClaude
	case strings.HasPrefix(id, "amd."):
		return HybridFromGateway
	case strings.HasPrefix(id, "fireworks."):
		return HybridFromFireworks
	}
	return HybridFromLocal
}

var nameParts = regexp.MustCompile(`[-_]`)

// ShortModelName is short_name: a model's family for a label, so
// amd.Claude-Opus-5-5 is Opus and amd.DeepSeek-V4.1-Flash is DeepSeek.
func ShortModelName(id string) string {
	bare := strings.TrimSpace(id)
	if p := HybridProviderOf(bare); p != HybridFromLocal && p != HybridFromClaude {
		bare = bare[strings.Index(bare, ".")+1:]
	}
	bare = strings.TrimPrefix(bare, "user.")
	bare = bare[strings.LastIndex(bare, "/")+1:]
	bare = strings.TrimPrefix(bare, "claude-")
	first := bare
	for _, part := range nameParts.Split(bare, -1) {
		if part != "" && strings.ToLower(part) != "claude" {
			first = part
			break
		}
	}
	if first == "" {
		return first
	}
	return strings.ToUpper(first[:1]) + first[1:]
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

// set reports whether provider p offers anything here.
func (c HybridCatalog) set(p string) bool {
	if p == HybridFromClaude {
		return c.ClaudeKey
	}
	return len(c.Models[p]) > 0
}

// Find returns the provider's own spelling of id, or why it can't be used now
// (Inventory.check).
func (c HybridCatalog) Find(id string) (string, error) {
	p := HybridProviderOf(id)
	if err := c.Errs[p]; err != nil {
		return "", fmt.Errorf("%s couldn't be checked: %v", hybridProviderNames[p], err)
	}
	models := c.Models[p]
	if p == HybridFromClaude && c.ClaudeKey {
		models = HybridClaudeModels
	}
	for _, have := range models {
		if strings.EqualFold(have, id) || (p == HybridFromFireworks && SameCloudModel(have, id)) ||
			(p == HybridFromLocal && SameLocalID(have, id)) {
			if p != HybridFromLocal && c.LocalOnly {
				return "", fmt.Errorf("Local only is on, so %s can't be used", id)
			}
			return have, nil
		}
	}
	switch {
	case p == HybridFromLocal:
		return "", fmt.Errorf("Download %s with /models", id)
	case c.LocalOnly:
		return "", fmt.Errorf("Local only is on, so %s can't be used", id)
	case !c.set(p):
		return "", fmt.Errorf("%s needs %s: %s", id, hybridProviderNames[p], hybridSetupHints[p])
	}
	return "", fmt.Errorf("%s doesn't offer %s", upperFirst(hybridProviderNames[p]), id)
}

// Checked is Inventory.checked: the providers set up here, and how to add more.
func (c HybridCatalog) Checked() string {
	var ready, missing []string
	for _, p := range HybridProviders {
		if c.set(p) {
			ready = append(ready, hybridProviderNames[p])
		} else {
			missing = append(missing, hybridSetupHints[p])
		}
	}
	text := "Checked " + strings.Join(ready, ", ") + "."
	if len(missing) > 0 {
		text += " To use more models: " + strings.Join(missing, "; ") + "."
	}
	return text
}

// HybridTemplate is a ready-made hybrid setup, as GET /api/hybrid/templates
// describes one.
type HybridTemplate struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	Description    string   `json:"description"`
	Cheap          string   `json:"cheap_model"`
	Strong         string   `json:"strong_model"`
	Classifier     string   `json:"classifier_model"`
	Policy         string   `json:"policy"`
	Providers      []string `json:"providers_needed"`
	Available      bool     `json:"available"`
	Reason         string   `json:"reason"`
	Measured       bool     `json:"measured"`
	MeasuredNote   string   `json:"measured_note"`
	ClassifierNote string   `json:"classifier_note"`
}

// Settings is base with the template's models and policy; a preset policy
// brings its own rules.
func (t HybridTemplate) Settings(base HybridSettings) HybridSettings {
	base.Cheap, base.Strong, base.Classifier, base.Policy = t.Cheap, t.Strong, t.Classifier, t.Policy
	base.Rules = nil
	return base
}

// hybridTemplateDefs is TEMPLATES: id, name, description, policy.
var hybridTemplateDefs = []HybridTemplate{
	{ID: HybridMeasuredTemplate, Name: "Gateway: DeepSeek + Opus",
		Description: "DeepSeek V4.1 Flash for routine work, Claude Opus 5.5 for the rest, " +
			"both through the AMD LLM Gateway. The default."},
	{ID: "gateway-deepseek-sonnet", Name: "Gateway: DeepSeek + Sonnet",
		Description: "As the default, with Claude Sonnet 5.5 as the cheaper strong model."},
	{ID: "gateway-balanced", Name: "Gateway: DeepSeek + Opus, balanced",
		Description: "The default pair, also sending medium-difficulty code changes to DeepSeek.",
		Policy:      HybridPolicyBalanced},
	{ID: "local-plus-cloud", Name: "This PC + Opus",
		Description: "Your local chat model for routine work and as the classifier, Claude " +
			"Opus 5.5 through the gateway for hard work."},
	{ID: "all-local", Name: "All on this PC",
		Description: HybridSmallLocalModel + " for routine work and as the classifier, " + HybridLargeLocalModel +
			" for hard work. Nothing leaves this PC, so it works under Local only."},
	{ID: "fireworks", Name: "Fireworks + Opus",
		Description: "DeepSeek V4.1 Flash on Fireworks AI for routine work, Claude Opus 5.5 " +
			"(through the gateway, else Anthropic directly) for the rest."},
	{ID: "claude-direct", Name: "Anthropic: Haiku + Opus",
		Description: "Claude Haiku 5.5 for routine work, Claude Opus 5.5 for the rest, " +
			"straight from Anthropic."},
}

// members is _members: (cheap, strong) for template id on this PC; the
// classifier is always the cheap model.
func (c HybridCatalog) members(id string) (string, string) {
	switch id {
	case HybridMeasuredTemplate, "gateway-balanced":
		return DefaultHybridCheapModel, DefaultHybridStrongModel
	case "gateway-deepseek-sonnet":
		return DefaultHybridCheapModel, HybridSonnetModel
	case "local-plus-cloud":
		local := c.DefaultLocal
		if local == "" {
			local = HybridSmallLocalModel
		}
		return local, DefaultHybridStrongModel
	case "all-local":
		return HybridSmallLocalModel, HybridLargeLocalModel
	case "fireworks":
		_, gatewayErr := c.Find(DefaultHybridStrongModel)
		_, claudeErr := c.Find(HybridClaudeDirectStrong)
		if gatewayErr != nil && claudeErr == nil {
			return HybridFireworksCheap, HybridClaudeDirectStrong
		}
		return HybridFireworksCheap, DefaultHybridStrongModel
	}
	return HybridClaudeDirectCheap, HybridClaudeDirectStrong
}

// BuiltinHybridTemplates are the seven templates, judged against what c offers.
func BuiltinHybridTemplates(c HybridCatalog) []HybridTemplate {
	out := make([]HybridTemplate, 0, len(hybridTemplateDefs))
	for _, def := range hybridTemplateDefs {
		out = append(out, c.describe(def))
	}
	return out
}

// describe is describe(): the template resolved here, in each provider's own
// spelling, with whether it can be used now.
func (c HybridCatalog) describe(t HybridTemplate) HybridTemplate {
	if t.Policy == "" {
		t.Policy = HybridPolicyQuality
	}
	t.Cheap, t.Strong = c.members(t.ID)
	t.Classifier = t.Cheap
	for _, m := range []string{t.Cheap, t.Strong} {
		if p := HybridProviderOf(m); !contains(t.Providers, p) {
			t.Providers = append(t.Providers, p)
		}
	}
	reason := c.availability(t.Cheap, t.Strong)
	if t.ID == "fireworks" && reason != "" {
		_, strongErr := c.Find(t.Strong)
		_, claudeErr := c.Find(HybridClaudeDirectStrong)
		if strongErr != nil && claudeErr != nil {
			reason += ". The strong model needs the AMD LLM Gateway or an Anthropic key"
		}
	}
	t.Available, t.Reason = reason == "", reason
	if t.Available {
		t.Cheap, _ = c.Find(t.Cheap)
		t.Strong, _ = c.Find(t.Strong)
		t.Classifier = t.Cheap
	}
	t.Measured, t.MeasuredNote, t.ClassifierNote = HybridNotes(HybridSettings{
		Cheap: t.Cheap, Strong: t.Strong, Classifier: t.Classifier, Policy: t.Policy})
	return t
}

// availability is availability(): "" when every model can be used now.
func (c HybridCatalog) availability(models ...string) string {
	var unique, cloud []string
	for _, m := range models {
		if contains(unique, m) {
			continue
		}
		unique = append(unique, m)
		if HybridProviderOf(m) != HybridFromLocal {
			cloud = append(cloud, m)
		}
	}
	if c.LocalOnly && len(cloud) > 0 {
		return "Local only is on, and this sends turns to the cloud (" + strings.Join(cloud, ", ") +
			"). Turn Local only off, or use the all-local pairing"
	}
	for _, m := range unique {
		if _, err := c.Find(m); err != nil {
			return err.Error()
		}
	}
	return ""
}

// HybridEntryLabel is picker_entry's name for the configured pair.
func HybridEntryLabel(h HybridModels) string {
	return "Hybrid (" + ShortModelName(h.Cheap) + " + " + ShortModelName(h.Strong) + ")"
}

// HybridNotes is notes(): whether s is the measured pair, and the notes the
// apps show for it.
func HybridNotes(s HybridSettings) (measured bool, measuredNote, classifierNote string) {
	h := s.Models()
	measured = h.Cheap == DefaultHybridCheapModel && h.Strong == DefaultHybridStrongModel &&
		h.Classifier == DefaultHybridCheapModel && s.Policy == HybridPolicyQuality
	if !measured {
		measuredNote = HybridMeasuredNote
	}
	if HybridProviderOf(h.Classifier) == HybridFromLocal {
		classifierNote = HybridClassifierNote
	}
	return measured, measuredNote, classifierNote
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func upperFirst(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

// DefaultLocalChatModel is local_default_model: config.json's default_model
// when it names a model on this PC, else Gemma.
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
	if id == "" || IsHybridID(id) || HybridProviderOf(id) != HybridFromLocal {
		return defaultChatModel, nil
	}
	return id, nil
}
