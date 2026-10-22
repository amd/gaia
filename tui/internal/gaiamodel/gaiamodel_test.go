// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package gaiamodel

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The test binary stands in for `gaia`: run with GAIAMODEL_STUB set, it records
// its arguments, prints GAIAMODEL_STDOUT and exits with GAIAMODEL_EXIT. That
// works on every OS, unlike a shell-script stub.
func TestMain(m *testing.M) {
	if os.Getenv("GAIAMODEL_STUB") != "" {
		_ = os.WriteFile(os.Getenv("GAIAMODEL_ARGS"), []byte(strings.Join(os.Args[1:], "\n")), 0o600)
		fmt.Fprint(os.Stderr, "[INFO] gaia.config | Saved GAIA config\n")
		fmt.Print(os.Getenv("GAIAMODEL_STDOUT"))
		code := 0
		fmt.Sscan(os.Getenv("GAIAMODEL_EXIT"), &code)
		os.Exit(code)
	}
	os.Exit(m.Run())
}

func stub(t *testing.T, stdout string, exit int) (args func() []string) {
	t.Helper()
	argsFile := filepath.Join(t.TempDir(), "args")
	t.Setenv("GAIAMODEL_STUB", "1")
	t.Setenv("GAIAMODEL_STDOUT", stdout)
	t.Setenv("GAIAMODEL_EXIT", fmt.Sprint(exit))
	t.Setenv("GAIAMODEL_ARGS", argsFile)
	original := Binary
	Binary = func() (string, error) { return os.Args[0], nil }
	t.Cleanup(func() { Binary = original })
	return func() []string {
		raw, err := os.ReadFile(argsFile)
		if err != nil {
			t.Fatalf("the stub never ran: %v", err)
		}
		return strings.Split(string(raw), "\n")
	}
}

const passed = `{"ok": true, "url": "unsloth/Qwen3-0.6B-GGUF",
 "checks": [{"id": "link", "label": "Hugging Face link", "ok": true, "detail": "unsloth/Qwen3-0.6B-GGUF"}],
 "error": null, "warnings": [], "variants": [{"quant": "Q4_K_M", "size_gb": 0.4, "fits": true}],
 "model_id": "user.Qwen3-0.6B-Q4_K_M-GGUF", "display_name": "Qwen3-0.6B Q4_K_M", "quant": "Q4_K_M", "size_gb": 0.4}`

func TestCheckLinkSendsTheLinkAndQuantAndReadsTheReport(t *testing.T) {
	args := stub(t, passed, 0)
	r, err := CheckLink(context.Background(), "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/tree/main", "Q4_K_M")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"model", "check", "--json", "--quant", "Q4_K_M", "--", "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/tree/main"}
	if got := args(); strings.Join(got, " ") != strings.Join(want, " ") {
		t.Errorf("args = %q, want %q", got, want)
	}
	if !r.OK || r.ModelID != "user.Qwen3-0.6B-Q4_K_M-GGUF" || len(r.Checks) != 1 || r.Variants[0].Quant != "Q4_K_M" {
		t.Errorf("report not decoded: %+v", r)
	}
}

func TestARefusedLinkIsAReportNotAnError(t *testing.T) {
	stub(t, `{"ok": false, "url": "unsloth/flint-GGUF", "error": "'flint' is not supported",
	 "checks": [{"id": "architecture", "label": "Supported architecture", "ok": false, "detail": "'flint' is not supported"}],
	 "warnings": [], "variants": []}`, 1)
	r, err := CheckLink(context.Background(), "unsloth/flint-GGUF", "")
	if err != nil {
		t.Fatalf("a refusal must come back as a report: %v", err)
	}
	if r.OK || r.Error != "'flint' is not supported" || r.Checks[0].OK {
		t.Errorf("refusal not decoded: %+v", r)
	}
}

func TestAddReadsTheNestedReport(t *testing.T) {
	args := stub(t, `{"model": {"id": "user.Qwen3-0.6B-Q4_K_M-GGUF"}, "report": `+passed+`}`, 0)
	r, err := Add(context.Background(), "unsloth/Qwen3-0.6B-GGUF", "")
	if err != nil {
		t.Fatal(err)
	}
	if got := args(); got[1] != "add" || len(got) != 5 {
		t.Errorf("args = %q; no --quant when none was chosen", got)
	}
	if !r.OK || r.DisplayName != "Qwen3-0.6B Q4_K_M" {
		t.Errorf("nested report not used: %+v", r)
	}
}

func TestTheLinkReachesTheCLIAsSchemeHostAndPathOnly(t *testing.T) {
	args := stub(t, passed, 0)
	link := "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF?_sm_nck=1&controls_id=7c0a#files"
	if _, err := CheckLink(context.Background(), link, ""); err != nil {
		t.Fatal(err)
	}
	if got := args(); got[len(got)-1] != "https://huggingface.co/unsloth/Qwen3-0.6B-GGUF" || got[len(got)-2] != "--" {
		t.Errorf("args = %q", got)
	}
}

func TestALinkWithShellCharactersNeverRuns(t *testing.T) {
	stub(t, passed, 0)
	original := Binary
	ran := false
	Binary = func() (string, error) { ran = true; return original() }
	for _, link := range []string{
		`https://huggingface.co/a/b" & calc & "`,
		`unsloth/x|whoami`,
		`--version`,
		`file:///etc/passwd`,
		`a/b"c`,
	} {
		if _, err := CheckLink(context.Background(), link, ""); err == nil {
			t.Errorf("%q was accepted", link)
		}
	}
	if _, err := CheckLink(context.Background(), "unsloth/x", "Q4 & calc"); err == nil {
		t.Error("a quant with shell characters was accepted")
	}
	if _, err := Remove(context.Background(), "user.x & calc"); err == nil {
		t.Error("a model id with shell characters was accepted")
	}
	if ran {
		t.Error("the CLI ran for a refused argument")
	}
}

func TestRemoveAndItsRefusal(t *testing.T) {
	stub(t, `{"removed": "user.X-GGUF", "display_name": "X", "cleared": ["last_model"]}`, 0)
	r, err := Remove(context.Background(), "user.X-GGUF")
	if err != nil || r.ID != "user.X-GGUF" || r.Cleared[0] != "last_model" {
		t.Fatalf("remove: %+v, %v", r, err)
	}
	stub(t, `{"ok": false, "error": "Gemma is not a model you added"}`, 1)
	if _, err := Remove(context.Background(), "Gemma"); err == nil || !strings.Contains(err.Error(), "not a model you added") {
		t.Fatalf("want the CLI's reason, got %v", err)
	}
}

func TestAServerFailureIsAnErrorWithTheReason(t *testing.T) {
	stub(t, `{"ok": false, "error": "The local model server did not answer"}`, 2)
	_, err := CheckLink(context.Background(), "unsloth/Qwen3-0.6B-GGUF", "")
	if err == nil || !strings.Contains(err.Error(), "gaia model") {
		t.Fatalf("want an error naming the command, got %v", err)
	}
}

func TestNoJSONIsAnError(t *testing.T) {
	stub(t, "usage: gaia [-h]", 0)
	if _, err := CheckLink(context.Background(), "x/y", ""); err == nil || !strings.Contains(err.Error(), "no JSON") {
		t.Fatalf("want a no-JSON error, got %v", err)
	}
}
