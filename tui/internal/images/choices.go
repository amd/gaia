// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package images is the image model, size and steps a user can pick, read off
// Lemonade's catalog. It mirrors gaia.sd.settings, so the TUI offers and
// refuses exactly what the Agent UI does.
package images

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/amd/gaia/tui/internal/lemonade"
)

//go:embed sizes.json
var sizesJSON []byte

type sizesDoc struct {
	CandidateSizes []string `json:"candidate_sizes"`
	LegacyModels   []string `json:"legacy_models"`
	LegacySizes    []string `json:"legacy_sizes"`
	MaxSteps       int      `json:"max_steps"`
	DefaultModel   string   `json:"default_model"`
}

var sizes = func() sizesDoc {
	var d sizesDoc
	if err := json.Unmarshal(sizesJSON, &d); err != nil {
		panic("images: sizes.json is not valid: " + err.Error())
	}
	return d
}()

// MaxSteps is the most steps a saved setting may ask for.
var MaxSteps = sizes.MaxSteps

// DefaultModel is what the flagship creates images with when none is saved.
var DefaultModel = sizes.DefaultModel

// imageRecipes are the recipes that create images from a prompt.
var imageRecipes = map[string]bool{"sd-cpp": true, "thenoise": true}

// Choice is one model a user may pick, with the sizes it can make.
type Choice struct {
	ID                  string
	Recipe              string
	Downloaded          bool
	DownloadsOnFirstUse bool
	SizeGB              float64
	DefaultSize         string
	DefaultSteps        int
	Sizes               []string
}

func isLegacy(id string) bool {
	for _, m := range sizes.LegacyModels {
		if m == id {
			return true
		}
	}
	return false
}

func hasLabel(labels []string, want string) bool {
	for _, l := range labels {
		if l == want {
			return true
		}
	}
	return false
}

// number reads one image_defaults field, or def when it is absent or zero —
// Python's `int(x or def)`.
func number(defaults map[string]any, key string, def int) int {
	if v, ok := defaults[key].(float64); ok && int(v) != 0 {
		return int(v)
	}
	return def
}

func modelChoice(e lemonade.ImageModel) (Choice, bool) {
	var defaults map[string]any
	if len(e.ImageDefaults) == 0 || json.Unmarshal(e.ImageDefaults, &defaults) != nil || defaults == nil {
		return Choice{}, false
	}
	if !hasLabel(e.Labels, "image") || !imageRecipes[e.Recipe] || hasLabel(e.Labels, "upscaling") {
		return Choice{}, false
	}
	width := number(defaults, "width", 512)
	height := number(defaults, "height", 512)
	defaultSize := fmt.Sprintf("%dx%d", width, height)
	legacy := isLegacy(e.ID)
	var offered []string
	if legacy {
		// The SD client accepts only these sizes for the legacy models.
		offered = append(offered, sizes.LegacySizes...)
	} else {
		limit := number(defaults, "soft_pixel_cap", width*height)
		for _, s := range sizes.CandidateSizes {
			w, h, _ := ParseSize(s)
			if w*h <= limit {
				offered = append(offered, s)
			}
		}
	}
	if !contains(offered, defaultSize) {
		offered = append([]string{defaultSize}, offered...)
	}
	return Choice{
		ID:                  e.ID,
		Recipe:              e.Recipe,
		Downloaded:          e.Downloaded,
		DownloadsOnFirstUse: legacy && !e.Downloaded,
		SizeGB:              e.Size,
		DefaultSize:         defaultSize,
		DefaultSteps:        number(defaults, "steps", 20),
		Sizes:               offered,
	}, true
}

// Choices is the models a user may pick to create images: downloaded ones,
// and legacy SD models the SD client downloads on first use. Upscalers are
// left out. Downloaded ones come first.
func Choices(catalog []lemonade.ImageModel) []Choice {
	var out []Choice
	for _, e := range catalog {
		c, ok := modelChoice(e)
		if ok && (c.Downloaded || c.DownloadsOnFirstUse) {
			out = append(out, c)
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Downloaded != out[j].Downloaded {
			return out[i].Downloaded
		}
		return strings.ToLower(out[i].ID) < strings.ToLower(out[j].ID)
	})
	return out
}

var sizePattern = regexp.MustCompile(`^([1-9][0-9]{1,4})x([1-9][0-9]{1,4})$`)

// ParseSize is (width, height) for a WIDTHxHEIGHT string.
func ParseSize(size string) (int, int, error) {
	m := sizePattern.FindStringSubmatch(strings.TrimSpace(size))
	if m == nil {
		return 0, 0, fmt.Errorf("Image size must be WIDTHxHEIGHT, such as 1024x1024, got %q.", size)
	}
	w, _ := strconv.Atoi(m[1])
	h, _ := strconv.Atoi(m[2])
	return w, h, nil
}

// Find is the choice for model, if this Lemonade offers it.
func Find(choices []Choice, model string) (Choice, bool) {
	for _, c := range choices {
		if c.ID == model {
			return c, true
		}
	}
	return Choice{}, false
}

// Check returns the choice for model after checking size and steps suit it.
// Nil size or steps means the model's own default.
func Check(choices []Choice, model string, size *string, steps *int) (Choice, error) {
	choice, ok := Find(choices, model)
	if !ok {
		ids := make([]string, 0, len(choices))
		for _, c := range choices {
			ids = append(ids, c.ID)
		}
		offered := strings.Join(ids, ", ")
		if offered == "" {
			offered = "none"
		}
		return Choice{}, fmt.Errorf("%s is not an image model on this Lemonade, or it is not "+
			"downloaded. Models you can use: %s.", model, offered)
	}
	if size != nil {
		if _, _, err := ParseSize(*size); err != nil {
			return Choice{}, err
		}
		if !contains(choice.Sizes, *size) {
			return Choice{}, fmt.Errorf("%s takes the sizes %s, not %s.",
				model, strings.Join(choice.Sizes, ", "), *size)
		}
	}
	if steps != nil && (*steps < 1 || *steps > MaxSteps) {
		return Choice{}, fmt.Errorf("Image steps must be from 1 to %d, got %d.", MaxSteps, *steps)
	}
	return choice, nil
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
