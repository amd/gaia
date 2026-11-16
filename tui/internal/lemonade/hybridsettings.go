// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"strings"

	"github.com/amd/gaia/tui/internal/filelock"
)

// Hybrid routing policies, as gaia.llm.hybrid names them.
const (
	HybridPolicyQuality  = "quality"
	HybridPolicyBalanced = "balanced"
	HybridPolicyCustom   = "custom"
)

// HybridPolicies are the policies in the order they are offered.
var HybridPolicies = []string{HybridPolicyQuality, HybridPolicyBalanced, HybridPolicyCustom}

// Classifier timeout bounds and default, as GaiaConfig checks them.
const (
	DefaultHybridTimeout = 15
	MaxHybridTimeout     = 120
	// MaxHybridDifficulty is the top of the classifier's 1-5 scale; a rule's
	// 0 means the cheap model is never used for that kind.
	MaxHybridDifficulty = 5
)

// HybridKinds are the request kinds the classifier labels, in display order.
// A custom rules table must name every one.
var HybridKinds = []string{
	"write_prose", "answer_question", "analyze_data", "operate_tools",
	"change_code", "find_defects", "plan_or_design", "other",
}

// HybridKindLabel is a kind in plain words.
func HybridKindLabel(kind string) string {
	switch kind {
	case "write_prose":
		return "writing"
	case "answer_question":
		return "question"
	case "analyze_data":
		return "data analysis"
	case "operate_tools":
		return "using tools"
	case "change_code":
		return "code change"
	case "find_defects":
		return "finding bugs"
	case "plan_or_design":
		return "planning"
	case "other":
		return "other"
	}
	return kind
}

// HybridRule says when one kind of request may run on the cheap model.
type HybridRule struct {
	// CheapMaxDifficulty is the hardest difficulty (1-5) the cheap model takes;
	// 0 means never.
	CheapMaxDifficulty int  `json:"cheap_max_difficulty"`
	AllowHighStakes    bool `json:"allow_high_stakes"`
}

// HybridRules maps each kind to its rule.
type HybridRules map[string]HybridRule

// Clone copies the table so an edit cannot reach the one it came from.
func (r HybridRules) Clone() HybridRules {
	out := make(HybridRules, len(r))
	for k, v := range r {
		out[k] = v
	}
	return out
}

// RunsCheap is the decision for a classified request: cheap when it is easy
// enough for its kind's rule, and low-stakes unless the rule allows otherwise.
func (r HybridRules) RunsCheap(kind string, difficulty int, stakes string) bool {
	rule, ok := r[kind]
	if !ok {
		return false
	}
	return difficulty <= rule.CheapMaxDifficulty && (stakes == "low" || rule.AllowHighStakes)
}

// HybridPreset is the rules table a named policy stands for, matching
// runs_cheap in gaia.llm.hybrid exactly. Nil for custom or an unknown name.
func HybridPreset(policy string) HybridRules {
	if policy != HybridPolicyQuality && policy != HybridPolicyBalanced {
		return nil
	}
	rules := HybridRules{
		"write_prose":     {CheapMaxDifficulty: 3},
		"answer_question": {CheapMaxDifficulty: 3},
		"analyze_data":    {CheapMaxDifficulty: 3},
		"operate_tools":   {CheapMaxDifficulty: 3},
		"change_code":     {CheapMaxDifficulty: 2},
		"find_defects":    {},
		"plan_or_design":  {},
		"other":           {},
	}
	if policy == HybridPolicyBalanced {
		rules["change_code"] = HybridRule{CheapMaxDifficulty: 3, AllowHighStakes: true}
	}
	return rules
}

// HybridSettings are every hybrid_* setting in config.json. An empty model
// means its default, as GaiaConfig stores it.
type HybridSettings struct {
	Cheap, Strong, Classifier string
	// Timeout is the classifier's limit in whole seconds.
	Timeout int
	Policy  string
	// Rules is the custom table; nil unless Policy is custom.
	Rules HybridRules
}

// DefaultHybridSettings are the settings with nothing configured.
func DefaultHybridSettings() HybridSettings {
	return HybridSettings{Timeout: DefaultHybridTimeout, Policy: HybridPolicyQuality}
}

// Models resolves the defaults: the classifier is the cheap model unless set.
func (s HybridSettings) Models() HybridModels {
	h := HybridModels{Cheap: s.Cheap, Strong: s.Strong, Classifier: s.Classifier}
	if h.Cheap == "" {
		h.Cheap = DefaultHybridCheapModel
	}
	if h.Strong == "" {
		h.Strong = DefaultHybridStrongModel
	}
	if h.Classifier == "" {
		h.Classifier = h.Cheap
	}
	return h
}

// EffectiveRules is the table decisions are made by: the preset's, or the
// custom one.
func (s HybridSettings) EffectiveRules() HybridRules {
	if s.Policy == HybridPolicyCustom {
		return s.Rules
	}
	return HybridPreset(s.Policy)
}

// Validate applies the checks gaia.config and HybridSettings apply, worded for
// this screen.
func (s HybridSettings) Validate() error {
	for _, f := range []struct{ key, value string }{
		{"hybrid_cheap_model", s.Cheap},
		{"hybrid_strong_model", s.Strong},
		{"hybrid_classifier_model", s.Classifier},
	} {
		if IsHybridID(f.value) {
			return fmt.Errorf("%s can't be 'hybrid', which would route to itself. Choose a concrete model", f.key)
		}
	}
	h := s.Models()
	if strings.EqualFold(h.Cheap, h.Strong) {
		return fmt.Errorf("the cheap and strong models are both %s, so there is nothing to route between. "+
			"Choose a different model for one of them", h.Cheap)
	}
	if s.Timeout < 1 || s.Timeout > MaxHybridTimeout {
		return fmt.Errorf("the classifier timeout must be 1-%d seconds, but it is %d. %d is the default",
			MaxHybridTimeout, s.Timeout, DefaultHybridTimeout)
	}
	switch s.Policy {
	case HybridPolicyQuality, HybridPolicyBalanced:
		return nil
	case HybridPolicyCustom:
		return validateRules(s.Rules)
	}
	return fmt.Errorf("the policy must be one of %s, but it is %q", strings.Join(HybridPolicies, ", "), s.Policy)
}

func validateRules(rules HybridRules) error {
	if rules == nil {
		return errors.New("the custom policy needs a rules table. Start from Quality or Balanced and edit it")
	}
	var missing []string
	for _, kind := range HybridKinds {
		rule, ok := rules[kind]
		if !ok {
			missing = append(missing, kind)
			continue
		}
		if rule.CheapMaxDifficulty < 0 || rule.CheapMaxDifficulty > MaxHybridDifficulty {
			return fmt.Errorf("the rule for %s allows the cheap model up to difficulty %d; use 0 (never) to %d",
				kind, rule.CheapMaxDifficulty, MaxHybridDifficulty)
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("the custom rules have no rule for %s; every kind needs one", strings.Join(missing, ", "))
	}
	for kind := range rules {
		if !knownKind(kind) {
			return fmt.Errorf("the custom rules name %q, which is not a kind the classifier uses (%s)",
				kind, strings.Join(HybridKinds, ", "))
		}
	}
	return nil
}

func knownKind(kind string) bool {
	for _, k := range HybridKinds {
		if k == kind {
			return true
		}
	}
	return false
}

// ReadHybridSettings reads every hybrid_* key from config.json. A missing file
// is the defaults; a malformed value is an error naming the key, as it is in
// Python.
func ReadHybridSettings() (HybridSettings, error) {
	path := ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return DefaultHybridSettings(), nil
	}
	raw, err := filelock.ReadFile(path)
	if os.IsNotExist(err) {
		return DefaultHybridSettings(), nil
	}
	if err != nil {
		return HybridSettings{}, fmt.Errorf("cannot read %s for the hybrid settings: %v", path, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return HybridSettings{}, fmt.Errorf("%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	return parseHybridSettings(doc, path)
}

func parseHybridSettings(doc map[string]any, path string) (HybridSettings, error) {
	if _, err := resolveHybrid(doc, path); err != nil {
		return HybridSettings{}, err
	}
	s := DefaultHybridSettings()
	// Keep "" for a default so saving does not pin today's default.
	for _, f := range []struct {
		key string
		out *string
	}{
		{"hybrid_cheap_model", &s.Cheap},
		{"hybrid_strong_model", &s.Strong},
		{"hybrid_classifier_model", &s.Classifier},
	} {
		if v, ok := doc[f.key].(string); ok {
			*f.out = strings.TrimSpace(v)
		}
	}
	if v, ok := doc["hybrid_classifier_timeout"]; ok && v != nil {
		n, isNum := v.(float64)
		if !isNum || n != math.Trunc(n) {
			return HybridSettings{}, fmt.Errorf("%s: 'hybrid_classifier_timeout' must be a whole number of seconds, "+
				"but it is %v. Set it in /hybrid, or run `gaia config set hybrid_classifier_timeout %d`",
				path, v, DefaultHybridTimeout)
		}
		s.Timeout = int(n)
	}
	if v, ok := doc["hybrid_policy"]; ok && v != nil {
		word, isStr := v.(string)
		word = strings.ToLower(strings.TrimSpace(word))
		if !isStr || !knownPolicy(word) {
			return HybridSettings{}, fmt.Errorf("%s: 'hybrid_policy' must be one of %s, but it is %v. "+
				"Set it in /hybrid, or run `gaia config set hybrid_policy %s`",
				path, strings.Join(HybridPolicies, ", "), v, HybridPolicyQuality)
		}
		s.Policy = word
	}
	if s.Policy == HybridPolicyCustom {
		rules, err := parseHybridRules(doc["hybrid_rules"], path)
		if err != nil {
			return HybridSettings{}, err
		}
		s.Rules = rules
	}
	if err := s.Validate(); err != nil {
		return HybridSettings{}, fmt.Errorf("%s: %v", path, err)
	}
	return s, nil
}

func knownPolicy(word string) bool {
	for _, p := range HybridPolicies {
		if p == word {
			return true
		}
	}
	return false
}

func parseHybridRules(v any, path string) (HybridRules, error) {
	fix := "Edit the rules in /hybrid, or set hybrid_policy back to quality"
	if v == nil {
		return nil, fmt.Errorf("%s: 'hybrid_policy' is custom but 'hybrid_rules' is missing. %s", path, fix)
	}
	table, ok := v.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s: 'hybrid_rules' must be a table of kinds, but it is %v. %s", path, v, fix)
	}
	rules := HybridRules{}
	for kind, raw := range table {
		entry, ok := raw.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s: hybrid_rules.%s must be {\"cheap_max_difficulty\": 0-5, "+
				"\"allow_high_stakes\": true|false}, but it is %v. %s", path, kind, raw, fix)
		}
		n, isNum := entry["cheap_max_difficulty"].(float64)
		if !isNum || n != math.Trunc(n) {
			return nil, fmt.Errorf("%s: hybrid_rules.%s.cheap_max_difficulty must be a whole number 0-5, "+
				"but it is %v. %s", path, kind, entry["cheap_max_difficulty"], fix)
		}
		stakes, isBool := entry["allow_high_stakes"].(bool)
		if !isBool {
			return nil, fmt.Errorf("%s: hybrid_rules.%s.allow_high_stakes must be true or false, "+
				"but it is %v. %s", path, kind, entry["allow_high_stakes"], fix)
		}
		rules[kind] = HybridRule{CheapMaxDifficulty: int(n), AllowHighStakes: stakes}
	}
	if err := validateRules(rules); err != nil {
		return nil, fmt.Errorf("%s: %v. %s", path, err, fix)
	}
	return rules, nil
}

// SaveHybridSettings validates s and writes it to config.json, leaving every
// other key as it was. Rules are written only for the custom policy, so a
// table saved earlier survives a switch to a preset and back.
func SaveHybridSettings(s HybridSettings) error {
	if err := s.Validate(); err != nil {
		return err
	}
	path := ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return errors.New("cannot locate ~/.gaia/config.json (no home directory), so the hybrid settings were not saved")
	}
	keys := map[string]any{
		"hybrid_cheap_model":        nullIfEmpty(s.Cheap),
		"hybrid_strong_model":       nullIfEmpty(s.Strong),
		"hybrid_classifier_model":   nullIfEmpty(s.Classifier),
		"hybrid_classifier_timeout": s.Timeout,
		"hybrid_policy":             s.Policy,
	}
	if s.Policy == HybridPolicyCustom {
		keys["hybrid_rules"] = s.Rules
	}
	return writeConfigKeys(path, keys)
}

func nullIfEmpty(s string) any {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return strings.TrimSpace(s)
}

// writeConfigKeys sets keys in config.json under the lock Python's
// GaiaConfig.save_keys takes, so neither writer drops the other's keys.
func writeConfigKeys(path string, keys map[string]any) error {
	lock, err := filelock.Acquire(path, filelock.Timeout)
	if err != nil {
		return fmt.Errorf("could not save GAIA config at %s: %w", path, err)
	}
	defer lock.Release()
	doc := map[string]any{}
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(raw, &doc); err != nil {
			return fmt.Errorf("%s is not valid JSON (%v) — fix or delete it, then save again", path, err)
		}
	case !os.IsNotExist(err):
		return fmt.Errorf("cannot read %s: %w", path, err)
	}
	for k, v := range keys {
		doc[k] = v
	}
	body, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return fmt.Errorf("cannot encode %s: %w", path, err)
	}
	if err := filelock.WriteAtomic(path, append(body, '\n'), 0o600); err != nil {
		return fmt.Errorf("cannot write %s: %w", path, err)
	}
	return nil
}

// ShortModelName is a model's family name for a one-line label: DeepSeek,
// Opus, Gemma. An unknown family keeps its id, without the provider prefix.
func ShortModelName(id string) string {
	name := strings.TrimSpace(id)
	if provider, rest, ok := strings.Cut(name, "."); ok && (provider == "amd" || provider == "fireworks") {
		name = rest
	}
	name = name[strings.LastIndex(name, "/")+1:]
	lower := strings.ToLower(name)
	for _, f := range []struct{ hint, label string }{
		{"deepseek", "DeepSeek"}, {"opus", "Opus"}, {"sonnet", "Sonnet"}, {"haiku", "Haiku"},
		{"gemma", "Gemma"}, {"qwen", "Qwen"}, {"glm", "GLM"}, {"kimi", "Kimi"},
		{"llama", "Llama"}, {"mistral", "Mistral"}, {"gpt", "GPT"},
	} {
		if strings.Contains(lower, f.hint) {
			return f.label
		}
	}
	return name
}

// HybridDisplayName names hybrid by its two models: "Hybrid (DeepSeek + Opus)".
func HybridDisplayName(h HybridModels) string {
	cheap, strong := ShortModelName(h.Cheap), ShortModelName(h.Strong)
	if cheap == strong {
		cheap, strong = trimProvider(h.Cheap), trimProvider(h.Strong)
	}
	return "Hybrid (" + cheap + " + " + strong + ")"
}

func trimProvider(id string) string {
	if provider, rest, ok := strings.Cut(id, "."); ok && (provider == "amd" || provider == "fireworks") {
		return rest
	}
	return id
}

// HybridWhere is "(cloud)" when any of hybrid's models runs off this PC, else
// "(local)".
func HybridWhere(h HybridModels) string {
	if len(h.CloudModels()) == 0 {
		return "(local)"
	}
	return "(cloud)"
}

// HybridRoute is one routing decision, as a turn's route record or a preview
// reports it.
type HybridRoute struct {
	Model      string
	Kind       string
	Difficulty int // 0 when the classifier gave none
	Stakes     string
	Classified bool
	Sticky     bool
}

// Line is the decision in a few plain words: "→ DeepSeek (writing, d1)".
func (r HybridRoute) Line() string {
	name := ShortModelName(r.Model)
	switch {
	case r.Sticky:
		return "→ " + name + " (kept on it after an earlier hard turn)"
	case !r.Classified:
		return "→ " + name + " (couldn't classify the request)"
	}
	why := []string{HybridKindLabel(r.Kind)}
	if r.Difficulty > 0 {
		why = append(why, fmt.Sprintf("d%d", r.Difficulty))
	}
	if r.Stakes == "high" {
		why = append(why, "high stakes")
	}
	return "→ " + name + " (" + strings.Join(why, ", ") + ")"
}
