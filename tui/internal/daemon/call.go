// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package daemon

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Call performs one authenticated daemon request and returns the 2xx body.
//
// start=false attaches only to a daemon that is already running; true starts
// one. alternative names what the user can do instead when the running daemon
// is too old to have the route. A non-2xx answer is returned as the daemon's own
// detail — a 503 there carries a platform-specific remedy worth passing through.
func Call(method, path string, body []byte, start bool, op, alternative string) ([]byte, error) {
	dc := New(Options{})
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	var (
		inst *Instance
		err  error
	)
	if start {
		inst, err = dc.StartOrAttach(ctx)
	} else {
		inst, err = dc.Attach(ctx)
	}
	if err != nil {
		return nil, fmt.Errorf("could not reach the GAIA daemon: %w", err)
	}

	req := Request{Method: method, Path: path, Body: body, Op: op}
	if body != nil {
		req.Header = http.Header{"Content-Type": []string{"application/json"}}
	}
	resp, _, err := dc.Do(ctx, inst, req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// ErrorDetail prefixes the status; IsRouteMissing matches on the bare
		// detail, which is what tells version skew from the route's own refusal.
		full := ErrorDetail(resp)
		bare := strings.TrimPrefix(full, fmt.Sprintf("HTTP %d: ", resp.StatusCode))
		if IsRouteMissing(path, resp.StatusCode, bare) {
			return nil, &RouteMissingError{Op: op, Path: path, Alternative: alternative}
		}
		return nil, fmt.Errorf("%s", full)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1<<16))
}
