// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package components

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The UI is borderless by decision, not by accident: "avoid borders, use
// background colors". A frame costs four columns and two rows per panel and
// draws the eye to the chrome instead of the words inside it. Panels are
// delimited by a filled band (see Panel) or by position and colour.
//
// Source-level rather than render-level on purpose — a style can be declared
// in one package and rendered in another, and the render tests only cover the
// panels a test happens to instantiate.
func TestNoPanelDeclaresABorder(t *testing.T) {
	root := filepath.Join("..", "..", "..", "internal")

	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for i, line := range strings.Split(string(src), "\n") {
			if strings.Contains(line, "Border(lipgloss.") {
				t.Errorf("%s:%d declares a border: %s\n"+
					"    Use components.Panel (a filled band + body) instead.",
					filepath.ToSlash(path), i+1, strings.TrimSpace(line))
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", root, err)
	}
}
