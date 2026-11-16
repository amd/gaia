// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// pythonRunsCheap is gaia.llm.hybrid.runs_cheap as it stands, the behaviour
// the quality and balanced presets must reproduce exactly.
func pythonRunsCheap(kind string, difficulty int, stakes, policy string) bool {
	if kind == "change_code" && policy == "balanced" && difficulty <= 3 {
		return true
	}
	if stakes != "low" {
		return false
	}
	switch kind {
	case "write_prose", "answer_question", "analyze_data", "operate_tools":
		if difficulty <= 3 {
			return true
		}
	}
	return kind == "change_code" && difficulty <= 2
}

func TestPresetsReproduceRunsCheapExactly(t *testing.T) {
	for _, policy := range []string{HybridPolicyQuality, HybridPolicyBalanced} {
		rules := HybridPreset(policy)
		if err := validateRules(rules); err != nil {
			t.Fatalf("%s preset is not a valid table: %v", policy, err)
		}
		for _, kind := range HybridKinds {
			for d := 1; d <= 5; d++ {
				for _, stakes := range []string{"low", "high"} {
					got, want := rules.RunsCheap(kind, d, stakes), pythonRunsCheap(kind, d, stakes, policy)
					if got != want {
						t.Errorf("%s %s d%d %s: cheap=%v, runs_cheap says %v", policy, kind, d, stakes, got, want)
					}
				}
			}
		}
	}
	if HybridPreset(HybridPolicyCustom) != nil {
		t.Error("custom has no preset")
	}
}

func TestPresetIsACopyNotShared(t *testing.T) {
	a := HybridPreset(HybridPolicyQuality)
	a["other"] = HybridRule{CheapMaxDifficulty: 5}
	if HybridPreset(HybridPolicyQuality)["other"].CheapMaxDifficulty != 0 {
		t.Fatal("editing a preset's table changed the preset")
	}
}

func TestReadHybridSettingsDefaults(t *testing.T) {
	useConfig(t, nil)
	s, err := ReadHybridSettings()
	if err != nil {
		t.Fatal(err)
	}
	if s.Timeout != DefaultHybridTimeout || s.Policy != HybridPolicyQuality || s.Rules != nil || s.Cheap != "" {
		t.Fatalf("defaults: %+v", s)
	}
	if h := s.Models(); h.Classifier != DefaultHybridCheapModel {
		t.Fatalf("classifier defaults to cheap: %+v", h)
	}
}

func customRules() map[string]any {
	out := map[string]any{}
	for _, k := range HybridKinds {
		out[k] = map[string]any{"cheap_max_difficulty": 2, "allow_high_stakes": false}
	}
	return out
}

func TestReadHybridSettingsRejectsWhatPythonRejects(t *testing.T) {
	missingKind := customRules()
	delete(missingKind, "other")
	unknownKind := customRules()
	unknownKind["poetry"] = map[string]any{"cheap_max_difficulty": 1, "allow_high_stakes": false}
	tooHard := customRules()
	tooHard["other"] = map[string]any{"cheap_max_difficulty": 6, "allow_high_stakes": false}
	negative := customRules()
	negative["other"] = map[string]any{"cheap_max_difficulty": -1, "allow_high_stakes": false}
	notBool := customRules()
	notBool["other"] = map[string]any{"cheap_max_difficulty": 1, "allow_high_stakes": "yes"}
	fraction := customRules()
	fraction["other"] = map[string]any{"cheap_max_difficulty": 1.5, "allow_high_stakes": false}

	for name, tc := range map[string]struct {
		doc  map[string]any
		want string
	}{
		"custom without rules": {map[string]any{"hybrid_policy": "custom"}, "'hybrid_rules' is missing"},
		"rules not a table":    {map[string]any{"hybrid_policy": "custom", "hybrid_rules": []any{1}}, "must be a table"},
		"missing kind":         {map[string]any{"hybrid_policy": "custom", "hybrid_rules": missingKind}, "no rule for other"},
		"unknown kind":         {map[string]any{"hybrid_policy": "custom", "hybrid_rules": unknownKind}, `"poetry"`},
		"difficulty 6":         {map[string]any{"hybrid_policy": "custom", "hybrid_rules": tooHard}, "0 (never) to 5"},
		"difficulty -1":        {map[string]any{"hybrid_policy": "custom", "hybrid_rules": negative}, "0 (never) to 5"},
		"stakes not bool":      {map[string]any{"hybrid_policy": "custom", "hybrid_rules": notBool}, "true or false"},
		"fractional":           {map[string]any{"hybrid_policy": "custom", "hybrid_rules": fraction}, "whole number 0-5"},
		"bad policy":           {map[string]any{"hybrid_policy": "cheapest"}, "must be one of quality, balanced, custom"},
		"timeout 0":            {map[string]any{"hybrid_classifier_timeout": 0}, "1-120 seconds"},
		"timeout 121":          {map[string]any{"hybrid_classifier_timeout": 121}, "1-120 seconds"},
		"timeout fraction":     {map[string]any{"hybrid_classifier_timeout": 2.5}, "whole number of seconds"},
		"timeout text":         {map[string]any{"hybrid_classifier_timeout": "15"}, "whole number of seconds"},
		"same models":          {map[string]any{"hybrid_cheap_model": "amd.x", "hybrid_strong_model": "amd.x"}, "nothing to route between"},
		"routes to itself":     {map[string]any{"hybrid_strong_model": "hybrid"}, "route to itself"},
	} {
		t.Run(name, func(t *testing.T) {
			useConfig(t, tc.doc)
			_, err := ReadHybridSettings()
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err = %v, want it to say %q", err, tc.want)
			}
		})
	}
}

func TestReadHybridSettingsCustomAndPolicyCase(t *testing.T) {
	useConfig(t, map[string]any{"hybrid_policy": " Custom ", "hybrid_rules": customRules(), "hybrid_classifier_timeout": 30})
	s, err := ReadHybridSettings()
	if err != nil {
		t.Fatal(err)
	}
	if s.Policy != HybridPolicyCustom || s.Timeout != 30 || s.Rules["change_code"].CheapMaxDifficulty != 2 {
		t.Fatalf("got %+v", s)
	}
	if !s.EffectiveRules().RunsCheap("other", 2, "low") || s.EffectiveRules().RunsCheap("other", 2, "high") {
		t.Fatal("custom rules are not the ones decisions use")
	}
}

// A table left from an earlier custom policy is not checked while a preset is
// in force: the router does not use it then.
func TestRulesAreIgnoredUnderAPreset(t *testing.T) {
	useConfig(t, map[string]any{"hybrid_policy": "balanced", "hybrid_rules": "junk"})
	s, err := ReadHybridSettings()
	if err != nil || s.Rules != nil {
		t.Fatalf("%+v %v", s, err)
	}
	if !s.EffectiveRules().RunsCheap("change_code", 3, "high") {
		t.Fatal("balanced's own table is not the effective one")
	}
}

func TestSaveHybridSettingsKeepsOtherKeysAndRoundTrips(t *testing.T) {
	path := useConfig(t, map[string]any{"default_model": "hybrid", "full_access": true})
	rules := HybridPreset(HybridPolicyBalanced)
	rules["other"] = HybridRule{CheapMaxDifficulty: 1, AllowHighStakes: true}
	want := HybridSettings{Cheap: "amd.small", Strong: "", Classifier: "amd.tiny", Timeout: 20,
		Policy: HybridPolicyCustom, Rules: rules}
	if err := SaveHybridSettings(want); err != nil {
		t.Fatal(err)
	}
	got, err := ReadHybridSettings()
	if err != nil {
		t.Fatal(err)
	}
	if got.Cheap != "amd.small" || got.Strong != "" || got.Classifier != "amd.tiny" || got.Timeout != 20 ||
		got.Policy != HybridPolicyCustom || got.Rules["other"] != want.Rules["other"] {
		t.Fatalf("round trip: %+v", got)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if doc["default_model"] != "hybrid" || doc["full_access"] != true {
		t.Fatalf("other keys lost: %v", doc)
	}
	if v, ok := doc["hybrid_strong_model"]; !ok || v != nil {
		t.Fatalf("an empty model is stored as null, the default: %v", v)
	}

	// Back to a preset: the custom table stays for next time.
	want.Policy, want.Rules = HybridPolicyQuality, nil
	if err := SaveHybridSettings(want); err != nil {
		t.Fatal(err)
	}
	raw, err = os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `"cheap_max_difficulty"`) || !strings.Contains(string(raw), `"quality"`) {
		t.Fatalf("saving a preset dropped the custom table or the policy: %s", raw)
	}
}

func TestSaveHybridSettingsValidatesFirst(t *testing.T) {
	path := useConfig(t, map[string]any{"hybrid_policy": "quality"})
	bad := DefaultHybridSettings()
	bad.Policy = HybridPolicyCustom
	if err := SaveHybridSettings(bad); err == nil || !strings.Contains(err.Error(), "needs a rules table") {
		t.Fatalf("err = %v", err)
	}
	bad = DefaultHybridSettings()
	bad.Cheap, bad.Strong = "amd.x", "AMD.X"
	if err := SaveHybridSettings(bad); err == nil {
		t.Fatal("cheap and strong the same model saved")
	}
	bad = DefaultHybridSettings()
	bad.Timeout = 0
	if err := SaveHybridSettings(bad); err == nil {
		t.Fatal("a 0 s timeout saved")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "custom") || strings.Contains(string(raw), "amd.x") {
		t.Fatalf("a refused save wrote anyway: %s", raw)
	}
}

func TestSaveHybridSettingsRefusesABrokenConfigFile(t *testing.T) {
	path := useConfig(t, nil)
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := SaveHybridSettings(DefaultHybridSettings()); err == nil || !strings.Contains(err.Error(), "not valid JSON") {
		t.Fatalf("err = %v", err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != "{not json" {
		t.Fatal("the user's file was overwritten")
	}
}

func TestHybridNames(t *testing.T) {
	h := HybridModels{Cheap: DefaultHybridCheapModel, Strong: DefaultHybridStrongModel, Classifier: DefaultHybridCheapModel}
	if got := HybridDisplayName(h); got != "Hybrid (DeepSeek + Opus)" {
		t.Errorf("display = %q", got)
	}
	if HybridWhere(h) != "(cloud)" {
		t.Error("gateway models are cloud")
	}
	local := HybridModels{Cheap: "Gemma-4-E4B-it-GGUF", Strong: "Qwen3.6-35B-A3B-GGUF", Classifier: "Gemma-4-E4B-it-GGUF"}
	if HybridWhere(local) != "(local)" || HybridDisplayName(local) != "Hybrid (Gemma + Qwen)" {
		t.Errorf("local: %s %s", HybridDisplayName(local), HybridWhere(local))
	}
	same := HybridModels{Cheap: "amd.DeepSeek-V4.1-Flash", Strong: "amd.DeepSeek-V4-Pro"}
	if got := HybridDisplayName(same); got != "Hybrid (DeepSeek-V4.1-Flash + DeepSeek-V4-Pro)" {
		t.Errorf("one family twice must still tell them apart: %q", got)
	}
	if ShortModelName("fireworks.accounts/fireworks/models/glm-5p3-flash") != "GLM" {
		t.Error("fireworks path form")
	}
	if ShortModelName("amd.Mystery-7B") != "Mystery-7B" {
		t.Error("an unknown family keeps its id")
	}
}

func TestHybridRouteLine(t *testing.T) {
	for _, tc := range []struct {
		r    HybridRoute
		want string
	}{
		{HybridRoute{Model: "amd.DeepSeek-V4.1-Flash", Kind: "write_prose", Difficulty: 1, Stakes: "low", Classified: true}, "→ DeepSeek (writing, d1)"},
		{HybridRoute{Model: "amd.Claude-Opus-5-5", Kind: "change_code", Difficulty: 3, Stakes: "high", Classified: true}, "→ Opus (code change, d3, high stakes)"},
		{HybridRoute{Model: "amd.Claude-Opus-5-5"}, "→ Opus (couldn't classify the request)"},
		{HybridRoute{Model: "amd.Claude-Opus-5-5", Sticky: true, Classified: true}, "→ Opus (kept on it after an earlier hard turn)"},
	} {
		if got := tc.r.Line(); got != tc.want {
			t.Errorf("%+v: %q, want %q", tc.r, got, tc.want)
		}
	}
}
