// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"strings"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// Run modes: Local or Cloud, the Agent UI's choice of where chat runs, kept in
// ~/.gaia/config.json as `run_mode` and `run_profiles`. The rules mirror
// src/gaia/run_modes.py; tests/unit/test_run_modes.py pins the constants below.

const (
	LocalMode = "local"
	CloudMode = "cloud"
	// DefaultLocalModel is DEFAULT_MODEL_NAME, Local's chat model when no
	// local default_model is saved.
	DefaultLocalModel = "Gemma-4-E4B-it-GGUF"
)

// RunModes lists the run modes in the order they are shown.
var RunModes = []string{LocalMode, CloudMode}

// RunModeState is what config.json says about the run modes.
type RunModeState struct {
	// Saved is each mode's chat model when it differs from that mode's default.
	Saved map[string]string
	// DefaultModel is config.json's default_model, and HybridCheap its
	// hybrid_cheap_model, which a `hybrid` default starts on.
	DefaultModel string
	HybridCheap  string
	Path         string
}

// ModeOf is the mode a chat model puts GAIA in: cloud models are Cloud.
func ModeOf(model string) string {
	if isCloudModel(model) {
		return CloudMode
	}
	return LocalMode
}

func isCloudModel(model string) bool {
	return lemonade.IsCloudID(model) || lemonade.IsHybridID(model) ||
		strings.HasPrefix(strings.ToLower(strings.TrimSpace(model)), "claude-")
}

// DefaultChat is the chat model mode uses until the user picks another:
// Local's is the saved default_model when that is a local model, else Gemma;
// Cloud's is the first model GAIA recommends.
func (s RunModeState) DefaultChat(mode string) string {
	if mode == CloudMode {
		return lemonade.TopRecommendation().ID
	}
	model := s.DefaultModel
	if lemonade.IsHybridID(model) {
		model = s.HybridCheap
	}
	if model != "" && !isCloudModel(model) {
		return model
	}
	return DefaultLocalModel
}

// ChatFor is the chat model switching to mode runs.
func (s RunModeState) ChatFor(mode string) string {
	if m := s.Saved[mode]; m != "" {
		return m
	}
	return s.DefaultChat(mode)
}

// ReadRunModeState reads the run modes from config.json. A missing file is no
// choice saved; a malformed run_profiles is an error, as it is to Python.
func ReadRunModeState() (RunModeState, error) {
	return readRunModeState(realHostProbe())
}

func readRunModeState(p hostProbe) (RunModeState, error) {
	out := RunModeState{Saved: map[string]string{}}
	path := configPath(p)
	out.Path = path
	if path == "" {
		return out, nil
	}
	read := p.readConfig
	if read == nil {
		read = p.readFile
	}
	raw, err := read(path)
	if errors.Is(err, fs.ErrNotExist) {
		return out, nil
	}
	if err != nil {
		return out, fmt.Errorf("cannot read %s: %w", path, err)
	}
	var cfg struct {
		DefaultModel *string         `json:"default_model"`
		HybridCheap  *string         `json:"hybrid_cheap_model"`
		Profiles     json.RawMessage `json:"run_profiles"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return out, fmt.Errorf("%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	if cfg.DefaultModel != nil {
		out.DefaultModel = strings.TrimSpace(*cfg.DefaultModel)
	}
	if cfg.HybridCheap != nil {
		out.HybridCheap = strings.TrimSpace(*cfg.HybridCheap)
	}
	saved, err := parseProfiles(cfg.Profiles, path)
	if err != nil {
		return out, err
	}
	out.Saved = saved
	return out, nil
}

func parseProfiles(raw json.RawMessage, path string) (map[string]string, error) {
	saved := map[string]string{}
	if len(raw) == 0 || string(raw) == "null" {
		return saved, nil
	}
	bad := fmt.Errorf("%s: 'run_profiles' must map local or cloud to {\"chat\": \"<model id>\"}; "+
		"fix it by hand, or delete the key to go back to each mode's default model", path)
	var profiles map[string]map[string]string
	if err := json.Unmarshal(raw, &profiles); err != nil {
		return nil, bad
	}
	for mode, profile := range profiles {
		if mode != LocalMode && mode != CloudMode {
			return nil, bad
		}
		for key, model := range profile {
			if key != "chat" || strings.TrimSpace(model) == "" {
				return nil, bad
			}
			saved[mode] = strings.TrimSpace(model)
		}
	}
	return saved, nil
}

// sameModel tells whether two ids name one model: case and a `user.` prefix
// don't matter, and Fireworks lists `fireworks.x` as
// `fireworks.accounts/fireworks/models/x` too.
func sameModel(a, b string) bool {
	if a == "" || b == "" {
		return false
	}
	return strings.EqualFold(modelKey(a), modelKey(b))
}

func modelKey(id string) string {
	id = strings.TrimPrefix(strings.TrimSpace(id), "user.")
	if provider, rest, ok := strings.Cut(id, "."); ok && lemonade.IsCloudID(id) {
		return provider + "." + rest[strings.LastIndex(rest, "/")+1:]
	}
	return id
}

// recordPick notes in the config document that model now runs chat, as
// run_modes.record_pick does: the mode follows the model, the mode being left
// keeps the model it ran, and a mode on its default stores nothing. A
// malformed run_profiles is refused, not rewritten: Python refuses it too.
func recordPick(doc map[string]any, model string) error {
	state := RunModeState{Saved: map[string]string{}}
	str := func(key string) string {
		v, _ := doc[key].(string)
		return strings.TrimSpace(v)
	}
	state.DefaultModel = str("default_model")
	state.HybridCheap = str("hybrid_cheap_model")
	if raw, ok := doc["run_profiles"]; ok {
		encoded, err := json.Marshal(raw)
		if err != nil {
			return fmt.Errorf("cannot read run_profiles: %w", err)
		}
		saved, err := parseProfiles(encoded, "config.json")
		if err != nil {
			return fmt.Errorf("%w. %s isn't saved as your model until it is fixed", err, model)
		}
		state.Saved = saved
	}
	set := func(mode, chat string) {
		if sameModel(chat, state.DefaultChat(mode)) {
			delete(state.Saved, mode)
		} else {
			state.Saved[mode] = chat
		}
	}
	mode := ModeOf(model)
	// The model chat ran until now: the last pick, else the default it started on.
	leaving := str("last_model")
	if leaving == "" {
		leaving = str("default_model")
	}
	if leaving != "" {
		if left := ModeOf(leaving); left != mode {
			set(left, leaving)
		}
	}
	set(mode, model)
	doc["run_mode"] = mode
	if len(state.Saved) == 0 {
		delete(doc, "run_profiles")
		return nil
	}
	profiles := map[string]any{}
	for m, chat := range state.Saved {
		profiles[m] = map[string]any{"chat": chat}
	}
	doc["run_profiles"] = profiles
	return nil
}
