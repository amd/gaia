// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

//go:build darwin || linux

package attention

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync/atomic"
	"time"
)

// startupGrace is how long a helper must survive to count as holding. Both
// helpers fail fast (no logind, no D-Bus) and run forever once they hold.
const startupGrace = 300 * time.Millisecond

// startHelper runs a helper that holds the keep-awake request for as long as
// it lives. stdin, when non-nil, is the pipe whose closing ends it; otherwise
// it is killed. lost hears about a helper that dies before it is released.
func startHelper(cmd *exec.Cmd, stdin io.Closer, lost func(error)) (func() error, error) {
	name := cmd.Args[0]
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("could not start %s: %w", name, err)
	}
	done := make(chan struct{})
	var waitErr error
	go func() {
		waitErr = cmd.Wait()
		close(done)
	}()
	stopped := func() string {
		return fmt.Sprintf("%v: %s", waitErr, strings.TrimSpace(stderr.String()))
	}

	select {
	case <-done:
		return nil, fmt.Errorf("%s stopped straight away (%s)", name, stopped())
	case <-time.After(startupGrace):
	}

	var releasing atomic.Bool
	go func() {
		<-done
		if !releasing.Load() {
			lost(fmt.Errorf("%s stopped in the middle of a turn (%s)", name, stopped()))
		}
	}()

	return func() error {
		releasing.Store(true)
		if stdin != nil {
			_ = stdin.Close()
		} else if err := cmd.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
			return fmt.Errorf("could not stop %s: %w", name, err)
		}
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
		return nil
	}, nil
}
