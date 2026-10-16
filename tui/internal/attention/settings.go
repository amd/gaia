// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
)

// Settings are the user's choices, kept in ~/.gaia/config.json next to
// reduced_motion. Every one is on unless the user turns it off with
// `gaia config set <key> false`.
type Settings struct {
	Approval  bool
	Question  bool
	Finished  bool
	KeepAwake bool
}

// Defaults is every notification on and the PC kept awake.
func Defaults() Settings {
	return Settings{Approval: true, Question: true, Finished: true, KeepAwake: true}
}

// Notifies reports whether the user wants to hear about e.
func (s Settings) Notifies(e Event) bool {
	switch e {
	case EventApproval:
		return s.Approval
	case EventQuestion:
		return s.Question
	case EventFinished:
		return s.Finished
	}
	return false
}

// ParseSettings reads the keys from config.json's bytes. A key that is not a
// true/false value is an error naming the key, and the other keys still apply.
func ParseSettings(raw []byte) (Settings, error) {
	s := Defaults()
	if len(raw) == 0 {
		return s, nil
	}
	var cfg map[string]json.RawMessage
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return s, fmt.Errorf("it is not valid JSON (%v)", err)
	}
	var bad []error
	for _, f := range []struct {
		key string
		dst *bool
	}{
		{"notify_approval", &s.Approval},
		{"notify_question", &s.Question},
		{"notify_finished", &s.Finished},
		{"keep_awake", &s.KeepAwake},
	} {
		v, ok := cfg[f.key]
		if !ok || string(v) == "null" {
			continue
		}
		if err := json.Unmarshal(v, f.dst); err != nil {
			bad = append(bad, fmt.Errorf("%s must be true or false, not %s", f.key, v))
		}
	}
	return s, errors.Join(bad...)
}

// LoadSettings reads path. A missing file is the defaults.
func LoadSettings(path string) (Settings, error) {
	if path == "" {
		return Defaults(), nil
	}
	raw, err := os.ReadFile(path) // #nosec G304 -- the user's own ~/.gaia/config.json
	if errors.Is(err, fs.ErrNotExist) {
		return Defaults(), nil
	}
	if err != nil {
		return Defaults(), fmt.Errorf("cannot read %s: %w", path, err)
	}
	s, err := ParseSettings(raw)
	if err != nil {
		return s, fmt.Errorf("%s: %w", path, err)
	}
	return s, nil
}
