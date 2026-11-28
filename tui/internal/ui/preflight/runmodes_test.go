// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

const (
	qwen = "Qwen3.5-2B-GGUF"
	glm  = "fireworks.glm-5p3-flash"
)

func topCloudModel() string { return lemonade.TopRecommendation().ID }

func TestModeFollowsTheModel(t *testing.T) {
	for model, want := range map[string]string{
		qwen:                      LocalMode,
		"user.my-model-GGUF":      LocalMode,
		glm:                       CloudMode,
		"amd.DeepSeek-V4.1-Flash": CloudMode,
		"hybrid":                  CloudMode,
		"claude-sonnet-5":         CloudMode,
	} {
		if got := ModeOf(model); got != want {
			t.Errorf("ModeOf(%q) = %q, want %q", model, got, want)
		}
	}
}

func TestEachModeHasADefault(t *testing.T) {
	s := RunModeState{Saved: map[string]string{}}
	if got := s.ChatFor(LocalMode); got != DefaultLocalModel {
		t.Errorf("Local default = %q, want %q", got, DefaultLocalModel)
	}
	if got := s.ChatFor(CloudMode); got != topCloudModel() {
		t.Errorf("Cloud default = %q, want %q", got, topCloudModel())
	}
	s.DefaultModel = qwen
	if got := s.ChatFor(LocalMode); got != qwen {
		t.Errorf("a saved local default_model is Local's default; got %q", got)
	}
	s.DefaultModel = "amd.DeepSeek-V4.1-Flash"
	if got := s.ChatFor(LocalMode); got != DefaultLocalModel {
		t.Errorf("a cloud default_model is never Local's default; got %q", got)
	}
}

func TestReadRunModeStateReadsWhatPythonWrites(t *testing.T) {
	useConfig(t, `{"default_model": "`+qwen+`", "run_mode": "cloud",
		"run_profiles": {"cloud": {"chat": "`+glm+`"}}, "written_by_python": 1}`)
	s, err := ReadRunModeState()
	if err != nil {
		t.Fatal(err)
	}
	if s.ChatFor(CloudMode) != glm || s.ChatFor(LocalMode) != qwen {
		t.Fatalf("chat models = local %q, cloud %q", s.ChatFor(LocalMode), s.ChatFor(CloudMode))
	}
}

func TestAMissingConfigIsNoChoice(t *testing.T) {
	useConfig(t, "")
	s, err := ReadRunModeState()
	if err != nil || len(s.Saved) != 0 {
		t.Fatalf("got %+v, %v; want nothing saved and no error", s, err)
	}
}

func TestAMalformedRunProfilesIsReportedNotGuessed(t *testing.T) {
	for _, body := range []string{
		`{"run_profiles": {"hybrid": {"chat": "x"}}}`,
		`{"run_profiles": {"cloud": {"chat": ""}}}`,
		`{"run_profiles": {"cloud": {"speech": "x"}}}`,
		`{"run_profiles": ["cloud"]}`,
	} {
		useConfig(t, body)
		if _, err := ReadRunModeState(); err == nil || !strings.Contains(err.Error(), "run_profiles") {
			t.Errorf("%s: err = %v, want one naming run_profiles", body, err)
		}
	}
}

func TestASwitchToCloudRecordsTheModeAndKeepsLocalsModel(t *testing.T) {
	path := useConfig(t, `{"last_provider": "local", "last_model": "`+qwen+`", "full_access": true}`)
	if _, err := WriteLastModel("fireworks", glm); err != nil {
		t.Fatal(err)
	}
	doc := readDoc(t, path)
	if doc["run_mode"] != CloudMode || doc["last_model"] != glm || doc["full_access"] != true {
		t.Fatalf("doc = %v", doc)
	}
	want := map[string]any{
		"local": map[string]any{"chat": qwen},
		"cloud": map[string]any{"chat": glm},
	}
	if !reflect.DeepEqual(doc["run_profiles"], want) {
		t.Fatalf("run_profiles = %v, want %v", doc["run_profiles"], want)
	}
}

func TestSwitchingBackReopensOnTheModelEachModeWasLeftOn(t *testing.T) {
	useConfig(t, `{"last_model": "`+qwen+`"}`)
	for _, step := range [][2]string{{"fireworks", glm}, {"local", qwen}} {
		if _, err := WriteLastModel(step[0], step[1]); err != nil {
			t.Fatal(err)
		}
	}
	s, err := ReadRunModeState()
	if err != nil {
		t.Fatal(err)
	}
	if s.ChatFor(CloudMode) != glm || s.ChatFor(LocalMode) != qwen {
		t.Fatalf("after Cloud and back: local %q, cloud %q", s.ChatFor(LocalMode), s.ChatFor(CloudMode))
	}
}

func TestAModeOnItsDefaultStoresNothing(t *testing.T) {
	path := useConfig(t, `{"last_model": "`+DefaultLocalModel+`"}`)
	// Fireworks' long id for the recommended model is still Cloud's default.
	long := "fireworks.accounts/fireworks/models/" + strings.TrimPrefix(topCloudModel(), "fireworks.")
	if _, err := WriteLastModel("fireworks", long); err != nil {
		t.Fatal(err)
	}
	doc := readDoc(t, path)
	if _, ok := doc["run_profiles"]; ok {
		t.Fatalf("both modes are on their defaults, yet run_profiles = %v", doc["run_profiles"])
	}
	if doc["run_mode"] != CloudMode {
		t.Fatalf("run_mode = %v", doc["run_mode"])
	}
}

func TestASwitchWithinAModeLeavesTheOtherModeAlone(t *testing.T) {
	path := useConfig(t, `{"last_model": "`+glm+`", "run_profiles": {"local": {"chat": "`+qwen+`"}, "cloud": {"chat": "`+glm+`"}}}`)
	if _, err := WriteLastModel("fireworks", "fireworks.kimi-k3"); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"local": map[string]any{"chat": qwen},
		"cloud": map[string]any{"chat": "fireworks.kimi-k3"},
	}
	if got := readDoc(t, path)["run_profiles"]; !reflect.DeepEqual(got, want) {
		t.Fatalf("run_profiles = %v, want %v", got, want)
	}
}

func TestAMalformedRunProfilesStopsTheSaveAndIsLeftAsWritten(t *testing.T) {
	for _, body := range []string{
		`{"last_model": "` + qwen + `", "run_profiles": {"local": "` + qwen + `"}}`,
		`{"last_model": "` + qwen + `", "run_profiles": {"local": {"chat": "X", "vision": "Y"}}}`,
	} {
		path := useConfig(t, body)
		if _, err := WriteLastModel("fireworks", glm); err == nil || !strings.Contains(err.Error(), "run_profiles") {
			t.Errorf("%s: err = %v, want a refusal naming run_profiles", body, err)
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if string(raw) != body {
			t.Errorf("the file was rewritten:\n%s\nwant it left as\n%s", raw, body)
		}
	}
}

func TestWithNoLastModelTheDefaultIsTheModelBeingLeft(t *testing.T) {
	path := useConfig(t, `{"default_model": "fireworks.kimi-k3"}`)
	if _, err := WriteLastModel("local", qwen); err != nil {
		t.Fatal(err)
	}
	profiles, _ := readDoc(t, path)["run_profiles"].(map[string]any)
	if cloud, _ := profiles["cloud"].(map[string]any); cloud["chat"] != "fireworks.kimi-k3" {
		t.Fatalf("run_profiles = %v, want Cloud to keep fireworks.kimi-k3", profiles)
	}
}

func TestAHybridDefaultStartsLocalOnItsCheapModel(t *testing.T) {
	s := RunModeState{DefaultModel: "hybrid", HybridCheap: "Qwen3.5-4B-GGUF"}
	if got := s.DefaultChat(LocalMode); got != "Qwen3.5-4B-GGUF" {
		t.Errorf("Local default = %q, want the hybrid's local cheap model", got)
	}
	s.HybridCheap = "amd.DeepSeek-V4.1-Flash"
	if got := s.DefaultChat(LocalMode); got != DefaultLocalModel {
		t.Errorf("Local default = %q, want Gemma when the cheap model is in the cloud", got)
	}
}

func TestAConfigHoldingOnlyNullIsWrittenNotACrash(t *testing.T) {
	path := useConfig(t, "null")
	if _, err := WriteLastModel("local", qwen); err != nil {
		t.Fatal(err)
	}
	if doc := readDoc(t, path); doc["last_model"] != qwen || doc["run_mode"] != LocalMode {
		t.Fatalf("doc = %v", doc)
	}
}
