// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"strings"

	"github.com/amd/gaia/tui/internal/speech"
)

// ReadAloud is how replies are read aloud. The Agent UI shares these
// config.json keys, and Python's GaiaConfig validates them the same way
// (check_read_aloud_voice, check_read_aloud_speed).
type ReadAloud struct {
	Voice string // a Kokoro voice id; the default voice when unset
	Speed int    // percent of normal speed
	Auto  bool   // read each finished reply without being asked
	Path  string
}

// Settings is the voice and speed to speak with.
func (r ReadAloud) Settings() speech.Settings {
	return speech.Settings{Voice: r.Voice, Speed: r.Speed}
}

// ReadReadAloud returns the saved read-aloud settings, defaults filled in. A
// missing file or key is the default; a value GAIA would refuse is an error
// naming it, never quietly replaced.
func ReadReadAloud() (ReadAloud, error) {
	return readReadAloud(realHostProbe())
}

func readReadAloud(p hostProbe) (ReadAloud, error) {
	out := ReadAloud{Voice: speech.DefaultVoice(), Speed: speech.DefaultSpeed, Path: configPath(p)}
	if out.Path == "" {
		return out, nil
	}
	read := p.readConfig
	if read == nil {
		read = p.readFile
	}
	raw, err := read(out.Path)
	if errors.Is(err, fs.ErrNotExist) {
		return out, nil
	}
	if err != nil {
		return out, fmt.Errorf("cannot read %s: %w", out.Path, err)
	}
	var cfg struct {
		Voice *string          `json:"read_aloud_voice"`
		Speed *json.RawMessage `json:"read_aloud_speed"`
		Auto  *bool            `json:"read_aloud_auto"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return out, fmt.Errorf("the read-aloud settings in %s cannot be read (%v) — fix the file by hand or delete it", out.Path, err)
	}
	if cfg.Voice != nil {
		if v := strings.TrimSpace(*cfg.Voice); v != "" {
			if err := CheckReadAloudVoice(v); err != nil {
				return out, fmt.Errorf("%s: %w", out.Path, err)
			}
			out.Voice = v
		}
	}
	if cfg.Speed != nil && string(*cfg.Speed) != "null" {
		var speed int
		if err := json.Unmarshal(*cfg.Speed, &speed); err != nil {
			return out, fmt.Errorf("%s: read_aloud_speed must be a whole number from %d to %d, got %s",
				out.Path, speech.MinSpeed, speech.MaxSpeed, string(*cfg.Speed))
		}
		if err := CheckReadAloudSpeed(speed); err != nil {
			return out, fmt.Errorf("%s: %w", out.Path, err)
		}
		out.Speed = speed
	}
	if cfg.Auto != nil {
		out.Auto = *cfg.Auto
	}
	return out, nil
}

// CheckReadAloudVoice refuses an id Lemonade's kokoro-v1 does not have.
func CheckReadAloudVoice(id string) error {
	if _, ok := speech.LookupVoice(id); ok {
		return nil
	}
	ids := make([]string, 0, len(speech.Voices()))
	for _, v := range speech.Voices() {
		ids = append(ids, v.ID)
	}
	return fmt.Errorf("read_aloud_voice must be a Kokoro voice, got %q. Use one of: %s", id, strings.Join(ids, ", "))
}

// CheckReadAloudSpeed refuses a speed outside the range Python accepts.
func CheckReadAloudSpeed(speed int) error {
	if speed < speech.MinSpeed || speed > speech.MaxSpeed {
		return fmt.Errorf("read_aloud_speed is a percentage of normal speed from %d to %d, got %d",
			speech.MinSpeed, speech.MaxSpeed, speed)
	}
	return nil
}

// WriteReadAloudVoice saves the voice. Other keys in config.json are kept.
func WriteReadAloudVoice(id string) (string, error) {
	if err := CheckReadAloudVoice(id); err != nil {
		return "", err
	}
	return writeConfigKeys(map[string]any{"read_aloud_voice": id})
}

// WriteReadAloudSpeed saves the speed, a percentage of normal.
func WriteReadAloudSpeed(speed int) (string, error) {
	if err := CheckReadAloudSpeed(speed); err != nil {
		return "", err
	}
	return writeConfigKeys(map[string]any{"read_aloud_speed": speed})
}

// WriteReadAloudAuto saves whether finished replies are read automatically.
func WriteReadAloudAuto(on bool) (string, error) {
	return writeConfigKeys(map[string]any{"read_aloud_auto": on})
}
