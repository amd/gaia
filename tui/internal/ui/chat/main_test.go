// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/phone"
	"github.com/amd/gaia/tui/internal/setupapi"
	"github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// TestMain keeps the package off the developer's own GAIA home: a fake
// Lemonade on loopback would otherwise be taken for GAIA's server and its
// requests followed to the real one.
func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "gaia-chat-test-")
	if err != nil {
		panic(err)
	}
	os.Setenv("GAIA_HOME", home)
	// A developer's own Local only must not change what these tests send.
	os.Setenv("GAIA_POLICY_FILE", home+"/policy.json")
	os.Setenv("LEMONADE_BASE_URL", "")
	// Nor attach to, or start, the developer's own daemon.
	os.Setenv(daemon.EnvHome, filepath.Join(home, "daemon"))
	daemon.StubDefaultStartForTest(func(context.Context) (*exec.Cmd, error) {
		return nil, errors.New("test: no daemon")
	})
	lemonade.StubEnsureEmbeddedForTest(func(context.Context) error {
		return errors.New("test: no daemon")
	})
	// The setup screens and the /gpu probe must never start a real daemon.
	newChatSetupAPI = func() setupapi.API {
		return &setupapitest.Fake{PlanErr: errors.New("test: no daemon"), GPUErr: errors.New("test: no daemon")}
	}
	newGPUProbeAPI = newChatSetupAPI
	// Nor may the phone banner reach a real one: a daemon with no phone routes.
	newPhoneAPI = func(bool) *phone.Client {
		return phone.New(func(context.Context, string, string, []byte) (int, []byte, error) {
			return 404, []byte(`{"detail":"Not Found"}`), nil
		})
	}
	code := m.Run()
	os.RemoveAll(home)
	os.Exit(code)
}
