// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package update

import (
	"context"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"
)

func TestADownloadFromASilentServerGivesUp(t *testing.T) {
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		<-release
	}))
	defer srv.Close()
	defer close(release)

	previous := downloadClient
	downloadClient = newDownloadClient(100 * time.Millisecond)
	defer func() { downloadClient = previous }()

	done := make(chan error, 1)
	go func() {
		done <- download(context.Background(), srv.URL, filepath.Join(t.TempDir(), "installer"))
	}()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("download from a server that never answers reported success")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("download from a server that never answers did not give up")
	}
}
