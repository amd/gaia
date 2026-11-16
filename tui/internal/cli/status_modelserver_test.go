// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package cli

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestStatusNamesTheModelServerAndBackend(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/health"):
			_, _ = w.Write([]byte(`{"version":"2026.41.1","all_models_loaded":[{"model_name":"Gemma-4-E4B-it-GGUF","type":"llm","recipe":"llamacpp","recipe_options":{"ctx_size":65536}}]}`))
		case strings.HasSuffix(r.URL.Path, "/system-info"):
			_, _ = w.Write([]byte(`{"recipes":{"llamacpp":{"default_backend":"vulkan"}}}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	t.Setenv("GAIA_HOME", t.TempDir())

	var out bytes.Buffer
	printModelServer(&out, s.URL)

	got := out.String()
	if !strings.HasPrefix(got, "Model server:        Lemonade 2026.41.1 (yours) at 127.0.0.1:") ||
		!strings.Contains(got, "Gemma-4-E4B-it-GGUF on Vulkan (default), 64K ctx") {
		t.Fatalf("status line = %q", got)
	}
}

func TestStatusReportsADownModelServerOnOneLine(t *testing.T) {
	s := httptest.NewServer(http.NotFoundHandler())
	url := s.URL
	s.Close()
	t.Setenv("GAIA_HOME", t.TempDir())

	var out bytes.Buffer
	printModelServer(&out, url)

	if got := out.String(); strings.Count(got, "\n") != 1 || !strings.Contains(got, "not answering at") {
		t.Fatalf("status line = %q", got)
	}
}
