// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package catalog

import (
	_ "embed"
	"encoding/json"
	"fmt"
)

// Harness is a program that can answer as the GAIA agent (flagship_agent in config.json).
type Harness struct {
	Name     string `json:"name"`
	Native   bool   `json:"native"`
	Label    string `json:"label"`
	Status   string `json:"status"`
	Runnable bool   `json:"runnable"`
}

//go:embed harnesses.json
var harnessesJSON []byte

var harnesses = func() []Harness {
	var doc struct {
		Harnesses []Harness `json:"harnesses"`
	}
	if err := json.Unmarshal(harnessesJSON, &doc); err != nil {
		panic(fmt.Sprintf("catalog: harnesses.json is not valid: %v", err))
	}
	return doc.Harnesses
}()

// Harnesses lists every harness this build knows the name of, runnable or not.
func Harnesses() []Harness { return append([]Harness(nil), harnesses...) }

// HarnessNamed is the harness called name, and whether this build knows it.
func HarnessNamed(name string) (Harness, bool) {
	for _, h := range harnesses {
		if h.Name == name {
			return h, true
		}
	}
	return Harness{}, false
}
