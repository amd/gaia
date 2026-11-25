// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package catalog

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// UnprovenHarnessEnv names one known harness that may run before it is offered.
// Only the eval lanes and tests set it; it mirrors UNPROVEN_HARNESS_ENV in
// src/gaia/harnesses.py.
const UnprovenHarnessEnv = "GAIA_UNPROVEN_HARNESS"

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

// HarnessRunnable reports whether this build may start the harness called name:
// it is offered, or the eval override names it.
func HarnessRunnable(name string) bool {
	h, ok := HarnessNamed(name)
	if !ok {
		return false
	}
	return h.Runnable || strings.TrimSpace(os.Getenv(UnprovenHarnessEnv)) == name
}

// NativeHarness reports whether name is a native harness this build knows.
func NativeHarness(name string) bool {
	h, ok := HarnessNamed(name)
	return ok && h.Native
}

// HarnessNamed is the harness called name, and whether this build knows it.
func HarnessNamed(name string) (Harness, bool) {
	for _, h := range harnesses {
		if h.Name == name {
			return h, true
		}
	}
	return Harness{}, false
}
