// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package gateway

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// A gateway model Lemonade has "loaded" lists context_length 4096; its real
// window is max_context_window.
func TestGatewayModelCarriesTheProvidersWindow(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/models") {
			http.NotFound(w, r)
			return
		}
		fmt.Fprintf(w, `{"data":[{"id":"%s.DeepSeek-V4.1-Flash","recipe":"cloud","context_length":4096,"max_context_window":1048576}]}`, Provider)
	}))
	t.Cleanup(srv.Close)
	models, err := NewClientAt(srv.URL).ListModels()
	if err != nil {
		t.Fatal(err)
	}
	if len(models) != 1 || models[0].CtxSize != 1048576 {
		t.Fatalf("models = %+v, want one model with CtxSize 1048576", models)
	}
}
