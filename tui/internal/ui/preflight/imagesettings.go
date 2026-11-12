// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package preflight

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/amd/gaia/tui/internal/filelock"
)

// ImageSettings is the image model, size and steps saved in
// ~/.gaia/config.json, shared with the Agent UI and the flagship agent.
// Empty Model, nil Size or nil Steps mean the default.
type ImageSettings struct {
	Model string
	Size  *string
	Steps *int
}

// ReadImageSettings reads the saved image settings. A missing file means
// the defaults; an unreadable or invalid one is an error, as in Python.
func ReadImageSettings() (ImageSettings, error) {
	path := configPath(realHostProbe())
	if path == "" {
		return ImageSettings{}, fmt.Errorf("cannot locate ~/.gaia/config.json (no home directory)")
	}
	raw, err := filelock.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return ImageSettings{}, nil
	}
	if err != nil {
		return ImageSettings{}, fmt.Errorf("cannot read %s: %w", path, err)
	}
	var cfg struct {
		Model *string `json:"image_model"`
		Size  *string `json:"image_size"`
		Steps *int    `json:"image_steps"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return ImageSettings{}, fmt.Errorf("%s is not valid JSON, or an image_ setting in it has the wrong type (%v) — fix or delete it, then try again", path, err)
	}
	out := ImageSettings{Size: cfg.Size, Steps: cfg.Steps}
	if cfg.Model != nil {
		out.Model = *cfg.Model
	}
	return out, nil
}

// WriteImageSettings saves all three keys together, null meaning the default,
// the same shape the Agent UI's PUT /api/images/settings saves.
func WriteImageSettings(model string, size *string, steps *int) (string, error) {
	keys := map[string]any{"image_model": model, "image_size": nil, "image_steps": nil}
	if size != nil {
		keys["image_size"] = *size
	}
	if steps != nil {
		keys["image_steps"] = *steps
	}
	return writeConfigKeys(keys)
}
