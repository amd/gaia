// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// customFloorCtx is ADDED_MODEL_CTX in gaia.llm.custom_models: the window an
// added model loads with unless its own context is smaller.
const customFloorCtx = 32768

// customModel is one entry of config.json's `custom_models`, which
// gaia.llm.custom_models writes after a pasted Hugging Face link passes its
// checks. The TUI only reads it; adding and removing go through `gaia model`.
type customModel struct {
	ID            string  `json:"id"`
	DisplayName   string  `json:"display_name"`
	Checkpoint    string  `json:"checkpoint"`
	Recipe        string  `json:"recipe"`
	MMProj        string  `json:"mmproj"`
	Vision        bool    `json:"vision"`
	Reasoning     bool    `json:"reasoning"`
	SizeGB        float64 `json:"size_gb"`
	ContextLength int64   `json:"context_length"`
	SourceURL     string  `json:"source_url"`
}

// ReadCustomModels returns the models the user added from a Hugging Face link,
// as local recommendations that register on their first pull. A missing file
// or key is no models; a malformed one is an error, as it is in Python.
func ReadCustomModels() ([]Recommended, error) {
	path := ConfigPathFrom(os.Getenv, os.UserHomeDir)
	if path == "" {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("cannot read %s for your added models: %v", path, err)
	}
	return parseCustomModels(raw, path)
}

func parseCustomModels(raw []byte, path string) ([]Recommended, error) {
	var doc struct {
		Custom []customModel `json:"custom_models"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("%s cannot be read (%v) — fix the file by hand or delete it", path, err)
	}
	out := make([]Recommended, 0, len(doc.Custom))
	for _, c := range doc.Custom {
		if !strings.HasPrefix(c.ID, "user.") || c.Checkpoint == "" || c.Recipe == "" {
			return nil, fmt.Errorf("%s: an entry in custom_models has no user. id, checkpoint or recipe "+
				"(%q). Remove it with `gaia model remove`, or fix the file by hand", path, c.ID)
		}
		ctx := int64(customFloorCtx)
		if c.ContextLength > 0 && c.ContextLength < ctx {
			ctx = c.ContextLength
		}
		label := c.DisplayName
		if label == "" {
			label = strings.TrimPrefix(c.ID, "user.")
		}
		out = append(out, Recommended{
			ID:         c.ID,
			Provider:   "local",
			Label:      label,
			Note:       "added by you from " + c.SourceURL,
			RegisterAs: c.ID,
			SizeGB:     c.SizeGB,
			Checkpoint: c.Checkpoint,
			Recipe:     c.Recipe,
			MMProj:     c.MMProj,
			Vision:     c.Vision,
			Reasoning:  c.Reasoning,
			MinCtxSize: ctx,
			Custom:     true,
			SourceURL:  c.SourceURL,
		})
	}
	return out, nil
}

// SameLocalID compares two local model ids the way Lemonade names them: a
// registered `user.X` is listed as `X`, and case is not significant.
func SameLocalID(a, b string) bool {
	trim := func(s string) string {
		s = strings.ToLower(s)
		return strings.TrimPrefix(s, "user.")
	}
	return a != "" && b != "" && trim(a) == trim(b)
}
