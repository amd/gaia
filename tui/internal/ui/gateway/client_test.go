// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
package gateway

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

func TestHTTPErrorInstall404KeepsTheVersionHint(t *testing.T) {
	resp := &http.Response{
		StatusCode: http.StatusNotFound,
		Body: io.NopCloser(strings.NewReader(
			`{"error":{"message":"Cloud provider 'amd' is not installed.","type":"invalid_request"}}`)),
	}
	err := httpError(resp, http.MethodPost, "http://localhost/api/v1/install", "http://localhost/api/v1")
	if err == nil || !strings.Contains(err.Error(), "older than") ||
		strings.Contains(err.Error(), "gaia gateway install") {
		t.Fatalf("install 404 should keep the version hint: %v", err)
	}
}

func TestHTTPErrorClassifiesCloud404(t *testing.T) {
	cases := []struct {
		name    string
		body    string
		want    []string
		notWant []string
	}{
		{
			name:    "unregistered provider",
			body:    `{"error":{"message":"Cloud provider 'amd' is not installed. Call POST /v1/install first.","type":"invalid_request"}}`,
			want:    []string{"gaia gateway install", "no cloud provider registered"},
			notWant: []string{"older than"},
		},
		{
			name:    "detail wins over not_found type",
			body:    `{"error":{"message":"Cloud provider 'amd' is not installed. Call POST /v1/install first.","type":"not_found"}}`,
			want:    []string{"gaia gateway install"},
			notWant: []string{"older than"},
		},
		{
			name:    "old server with not_found type",
			body:    `{"error":{"message":"not found","type":"not_found"}}`,
			want:    []string{"older than", lemonade.MinExternalVersion, lemonade.UpgradeHint},
			notWant: []string{"gaia gateway install"},
		},
		{
			name:    "old server with no type",
			body:    `{"error":{"message":"missing"}}`,
			want:    []string{"older than", lemonade.MinExternalVersion},
			notWant: []string{"gaia gateway install"},
		},
		{
			name:    "other error keeps Lemonade's detail",
			body:    `{"error":{"message":"provider is misconfigured","type":"invalid_request"}}`,
			want:    []string{"provider is misconfigured"},
			notWant: []string{"older than", "gaia gateway install"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := &http.Response{
				StatusCode: http.StatusNotFound,
				Body:       io.NopCloser(strings.NewReader(tc.body)),
			}
			err := httpError(resp, http.MethodPost, "http://localhost/api/v1/cloud/auth", "http://localhost/api/v1")
			if err == nil {
				t.Fatal("expected an error")
			}
			for _, want := range tc.want {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error %q does not contain %q", err, want)
				}
			}
			for _, notWant := range tc.notWant {
				if strings.Contains(err.Error(), notWant) {
					t.Errorf("error %q unexpectedly contains %q", err, notWant)
				}
			}
		})
	}
}
