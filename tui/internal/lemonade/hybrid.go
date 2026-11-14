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

// The `hybrid` model id is virtual: each agent turn runs on a cheap or a strong
// concrete model, as gaia.llm.hybrid decides. Nothing named `hybrid` is ever
// downloaded or loaded; the concrete models are what must be available.
const (
	HybridModelID = "hybrid"
	// HybridProvider is the provider name a hybrid choice is saved under.
	HybridProvider = "hybrid"
	// Mirror DEFAULT_CHEAP_MODEL / DEFAULT_STRONG_MODEL in gaia.llm.hybrid.
	DefaultHybridCheapModel  = "amd.DeepSeek-V4.1-Flash"
	DefaultHybridStrongModel = "amd.Claude-Opus-5-5"
)

// IsHybridID reports whether id selects the hybrid router.
func IsHybridID(id string) bool { return strings.EqualFold(strings.TrimSpace(id), HybridModelID) }

// HybridRole is one concrete model hybrid uses, with the config key that sets it.
type HybridRole struct {
	Role  string
	Key   string
	Model string
}

// HybridModels are the concrete models a hybrid session runs on.
type HybridModels struct {
	Cheap, Strong, Classifier string
}

// Roles lists the concrete models once each, the classifier only when it is
// not the cheap model.
func (h HybridModels) Roles() []HybridRole {
	roles := []HybridRole{
		{Role: "cheap", Key: "hybrid_cheap_model", Model: h.Cheap},
		{Role: "strong", Key: "hybrid_strong_model", Model: h.Strong},
	}
	if !strings.EqualFold(h.Classifier, h.Cheap) && !strings.EqualFold(h.Classifier, h.Strong) {
		roles = append(roles, HybridRole{Role: "classifier", Key: "hybrid_classifier_model", Model: h.Classifier})
	}
	return roles
}

// ReadHybridModels reads the hybrid_* keys from config.json with the defaults
// HybridSettings.load applies: empty is the default, and the classifier is the
// cheap model unless set. A malformed value is an error, as it is in Python.
func ReadHybridModels() (HybridModels, error) {
	path := ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return resolveHybrid(nil, "")
	}
	raw, err := filelock.ReadFile(path)
	if os.IsNotExist(err) {
		return resolveHybrid(nil, path)
	}
	if err != nil {
		return HybridModels{}, fmt.Errorf("cannot read %s for the hybrid models: %v", path, err)
	}
	return parseHybridModels(raw, path)
}

func parseHybridModels(raw []byte, path string) (HybridModels, error) {
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return HybridModels{}, fmt.Errorf("%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	return resolveHybrid(doc, path)
}

func resolveHybrid(doc map[string]any, path string) (HybridModels, error) {
	get := func(key string) (string, error) {
		v, ok := doc[key]
		if !ok || v == nil {
			return "", nil
		}
		s, ok := v.(string)
		if !ok {
			return "", fmt.Errorf("%s: '%s' must be a model id, but it is %v. "+
				"Run `gaia config set %s <id>` (`gaia gateway models` lists gateway ids), or \"\" for the default", path, key, v, key)
		}
		s = strings.TrimSpace(s)
		if IsHybridID(s) {
			return "", fmt.Errorf("%s: '%s' can't be 'hybrid', which would route to itself. "+
				"Run `gaia config set %s <id>` with a concrete model id, or \"\" for the default", path, key, key)
		}
		return s, nil
	}
	var h HybridModels
	var err error
	if h.Cheap, err = get("hybrid_cheap_model"); err != nil {
		return HybridModels{}, err
	}
	if h.Strong, err = get("hybrid_strong_model"); err != nil {
		return HybridModels{}, err
	}
	if h.Classifier, err = get("hybrid_classifier_model"); err != nil {
		return HybridModels{}, err
	}
	if h.Cheap == "" {
		h.Cheap = DefaultHybridCheapModel
	}
	if h.Strong == "" {
		h.Strong = DefaultHybridStrongModel
	}
	if h.Classifier == "" {
		h.Classifier = h.Cheap
	}
	// Same refusal as HybridSettings in gaia.llm.hybrid.
	if h.Cheap == h.Strong {
		return HybridModels{}, fmt.Errorf("hybrid_cheap_model and hybrid_strong_model are both '%s', "+
			"so there is nothing to route between. Set one of them to another model, "+
			"or pick that model directly instead of 'hybrid'", h.Cheap)
	}
	return h, nil
}
