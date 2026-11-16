// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// pythonTemplatesSource is src/gaia/llm/hybrid_templates.py, the one definition
// of the templates; the Go copy is what the TUI judges with no Agent UI server.
func pythonTemplatesSource(t *testing.T) string {
	t.Helper()
	path := filepath.Join("..", "..", "..", "src", "gaia", "llm", "hybrid_templates.py")
	raw, err := os.ReadFile(path) // #nosec G304 -- fixed relative path to a checked-in source file
	if err != nil {
		t.Fatalf("could not read %s, the source the TUI's template copy mirrors: %v", path, err)
	}
	return strings.ReplaceAll(string(raw), "\r\n", "\n")
}

// pythonString is the value of a module-level string constant, with implicit
// concatenation of a parenthesised literal joined.
func pythonString(t *testing.T, src, name string) string {
	t.Helper()
	m := regexp.MustCompile(`(?m)^` + name + `\s*=\s*(\([^)]*\)|"[^"\n]*")`).FindStringSubmatch(src)
	if m == nil {
		t.Fatalf("hybrid_templates.py defines no %s", name)
	}
	return joinLiterals(m[1])
}

var pyLiteral = regexp.MustCompile(`f?"((?:[^"\\]|\\.)*)"|,`)

// joinLiterals joins adjacent string literals; a comma ends one argument.
func joinLiterals(code string) string {
	args := pythonArgs(code)
	return strings.Join(args, "")
}

// pythonArgs splits a call's string arguments, joining implicit concatenation.
func pythonArgs(code string) []string {
	var args []string
	cur, open := "", false
	for _, m := range pyLiteral.FindAllStringSubmatch(code, -1) {
		if m[0] == "," {
			if open {
				args = append(args, cur)
			}
			cur, open = "", false
			continue
		}
		cur += m[1]
		open = true
	}
	if open {
		args = append(args, cur)
	}
	return args
}

func TestHybridTemplatesMatchPython(t *testing.T) {
	src := pythonTemplatesSource(t)

	for name, want := range map[string]string{
		"GATEWAY_SONNET":    HybridSonnetModel,
		"FIREWORKS_CHEAP":   HybridFireworksCheap,
		"CLAUDE_CHEAP":      HybridClaudeDirectCheap,
		"CLAUDE_STRONG":     HybridClaudeDirectStrong,
		"SMALL_LOCAL":       HybridSmallLocalModel,
		"LARGE_LOCAL":       HybridLargeLocalModel,
		"MEASURED_TEMPLATE": HybridMeasuredTemplate,
		"MEASURED_NOTE":     HybridMeasuredNote,
		"CLASSIFIER_NOTE":   HybridClassifierNote,
	} {
		if got := pythonString(t, src, name); got != want {
			t.Errorf("%s is %q in Python, %q here", name, got, want)
		}
	}
	claude := regexp.MustCompile(`CLAUDE_MODELS = \(CLAUDE_CHEAP, "([^"]+)", CLAUDE_STRONG\)`).FindStringSubmatch(src)
	if claude == nil || strings.Join(HybridClaudeModels, ",") !=
		strings.Join([]string{HybridClaudeDirectCheap, claude[1], HybridClaudeDirectStrong}, ",") {
		t.Errorf("CLAUDE_MODELS differ: Python %v, here %v", claude, HybridClaudeModels)
	}

	// TEMPLATES: Template(id, name, description[, policy=...]) in order.
	start := strings.Index(src, "TEMPLATES = (")
	end := strings.Index(src, "TEMPLATE_IDS =")
	if start < 0 || end < start {
		t.Fatal("hybrid_templates.py has no TEMPLATES tuple")
	}
	blocks := strings.Split(src[start:end], "Template(")[1:]
	if len(blocks) != len(hybridTemplateDefs) {
		t.Fatalf("Python has %d templates, the TUI %d", len(blocks), len(hybridTemplateDefs))
	}
	subst := strings.NewReplacer("{SMALL_LOCAL}", HybridSmallLocalModel, "{LARGE_LOCAL}", HybridLargeLocalModel)
	for i, block := range blocks {
		def := hybridTemplateDefs[i]
		policy := HybridPolicyQuality
		if m := regexp.MustCompile(`policy="([a-z]+)"`).FindStringSubmatch(block); m != nil {
			policy = m[1]
			block = strings.Replace(block, m[0], "", 1)
		}
		args := pythonArgs(block)
		if strings.HasPrefix(strings.TrimSpace(block), "MEASURED_TEMPLATE") {
			args = append([]string{HybridMeasuredTemplate}, args...)
		}
		if len(args) != 3 {
			t.Fatalf("template %d: could not read id, name and description from %q", i, block)
		}
		want := HybridTemplate{ID: args[0], Name: args[1], Description: subst.Replace(args[2]), Policy: policy}
		got := def
		if got.Policy == "" {
			got.Policy = HybridPolicyQuality
		}
		if got.ID != want.ID || got.Name != want.Name || got.Description != want.Description || got.Policy != want.Policy {
			t.Errorf("template %d differs:\n Python %+v\n here   %+v", i, want, got)
		}
	}
}
