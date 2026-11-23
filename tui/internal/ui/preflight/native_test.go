package preflight

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/catalog"
	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/status"
)

// noPythonGAIA is a machine with no `gaia` CLI. Reaching for it fails the test:
// the native agent's checks must not depend on it.
func noPythonGAIA(t *testing.T) {
	t.Helper()
	stubGaiaInit(t, func() (string, error) {
		t.Error("the native agent's readiness check asked for the Python `gaia` CLI")
		return "", errors.New("no gaia CLI on this machine")
	})
}

func stubDownloadedLocalModels(t *testing.T, ids ...string) {
	t.Helper()
	orig := downloadedLocalModels
	t.Cleanup(func() { downloadedLocalModels = orig })
	downloadedLocalModels = func(context.Context) ([]lemonade.Model, error) {
		var out []lemonade.Model
		for _, id := range ids {
			out = append(out, lemonade.Model{ID: id, Downloaded: true})
		}
		return out, nil
	}
}

func fakeGaiacpp(t *testing.T) string {
	t.Helper()
	name := catalog.NativeAgentBinary
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// gaiacpp, a reachable Lemonade and a model it serves is everything a chat
// needs. No Python on the machine must not hold that launch, or mark it unsure.
func TestANativeAgentPassesTheGateWithNoPythonGAIA(t *testing.T) {
	isolateHome(t)
	noPythonGAIA(t)
	stubProbe(t, true)
	bin := fakeGaiacpp(t)

	t.Run("a cloud model", func(t *testing.T) {
		stubListedCloudModels(t, func(_ context.Context, provider string) ([]lemonade.Model, error) {
			if provider != "fireworks" {
				t.Errorf("asked for provider %q, want fireworks", provider)
			}
			return []lemonade.Model{{ID: "fireworks.deepseek-v4p1-flash", Recipe: "cloud"}}, nil
		})
		rep := localRunner{opts: LocalOptions{Binary: bin, Native: true, Model: "fireworks.deepseek-v4p1-flash"}}.
			Check(context.Background(), localCfg())
		assertAllRowsOK(t, rep)
		if !strings.Contains(rep.Chat, "fireworks.deepseek-v4p1-flash") {
			t.Errorf("the hand-off does not name the model: %q", rep.Chat)
		}
	})

	t.Run("a local model the user picked", func(t *testing.T) {
		stubDownloadedLocalModels(t, "Qwen3.6-35B-A3B-GGUF")
		rep := localRunner{opts: LocalOptions{Binary: bin, Native: true, Model: "Qwen3.6-35B-A3B-GGUF"}}.
			Check(context.Background(), localCfg())
		assertAllRowsOK(t, rep)
		if rep.ChatModel != "Qwen3.6-35B-A3B-GGUF" {
			t.Errorf("chat model = %q", rep.ChatModel)
		}
	})

	t.Run("no model chosen, the default downloaded", func(t *testing.T) {
		stubDownloadedLocalModels(t, nativeDefaultModel)
		rep := localRunner{opts: LocalOptions{Binary: bin, Native: true, AskWhereChatRuns: true}}.
			Check(context.Background(), localCfg())
		assertAllRowsOK(t, rep)
		if rep.ChatModel != nativeDefaultModel {
			t.Errorf("chat model = %q, want the one gaiacpp starts on", rep.ChatModel)
		}
	})
}

func assertAllRowsOK(t *testing.T, rep Report) {
	t.Helper()
	if rep.Blocked() {
		t.Fatalf("the gate refused a launch that can chat:\n%s", rep)
	}
	for _, row := range rep.Rows {
		if row.State != StateOK {
			t.Errorf("row %q is %s (%s); nothing here is unknown or failed:\n%s",
				row.Key, row.State.Word(), row.Line, rep)
		}
		if row.Key == KeyModel && row.Label != modelRowLabel {
			t.Errorf("the model row is labelled %q; gaiacpp needs no embedding model", row.Label)
		}
	}
}

// Not weaker, only without Python: a model the server cannot run still stops
// the launch, and the way forward is in the app.
func TestANativeAgentIsStillStoppedWhenItsModelCannotRun(t *testing.T) {
	isolateHome(t)
	noPythonGAIA(t)
	stubProbe(t, true)
	stubReplayKeptKey(t, func(context.Context, string) (bool, error) { return false, nil })
	bin := fakeGaiacpp(t)

	blockedOnModel := func(t *testing.T, opts LocalOptions) Row {
		t.Helper()
		opts.Binary, opts.Native = bin, true
		rep := localRunner{opts: opts}.Check(context.Background(), localCfg())
		blocker, blocked := rep.Blocker()
		if !blocked || blocker.Key != KeyModel || blocker.Disposition != status.DispositionHalt {
			t.Fatalf("a model that cannot run did not stop the launch:\n%s", rep)
		}
		if blocker.Fix == FixRunSetup || strings.Contains(blocker.Remedy.Command, "gaia init") {
			t.Errorf("the row offers Python setup, which this machine does not have: %+v", blocker)
		}
		if !strings.Contains(blocker.Remedy.Action, "Press p") {
			t.Errorf("the remedy is not something to do in the app: %q", blocker.Remedy.Action)
		}
		return blocker
	}

	t.Run("a cloud model its server does not list", func(t *testing.T) {
		stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) { return nil, nil })
		blockedOnModel(t, LocalOptions{Model: "fireworks.deepseek-v4p1-flash"})
	})
	t.Run("a picked local model that is not downloaded", func(t *testing.T) {
		stubDownloadedLocalModels(t, nativeDefaultModel)
		row := blockedOnModel(t, LocalOptions{Model: "Qwen3.6-35B-A3B-GGUF"})
		if !strings.Contains(row.Line, "Qwen3.6-35B-A3B-GGUF") {
			t.Errorf("the row does not name the model: %q", row.Line)
		}
	})
	t.Run("no model chosen and the default not downloaded", func(t *testing.T) {
		stubDownloadedLocalModels(t)
		row := blockedOnModel(t, LocalOptions{})
		if !strings.Contains(row.Line, nativeDefaultModel) {
			t.Errorf("the row does not name the model gaiacpp would start on: %q", row.Line)
		}
	})
	t.Run("a cloud provider chosen but not connected", func(t *testing.T) {
		rep := localRunner{opts: LocalOptions{Binary: bin, Native: true, ChatProvider: "fireworks"}}.
			Check(context.Background(), localCfg())
		blocker, blocked := rep.Blocker()
		if !blocked || blocker.Fix != FixConnectProvider || strings.Contains(blocker.Detail, "embedding") {
			t.Fatalf("an unconnected provider was not offered for connecting: %+v", blocker)
		}
	})
}

// The same machine on the Python agent still asks `gaia init`, as before.
func TestThePythonAgentStillAsksGaiaInit(t *testing.T) {
	asked := false
	stubGaiaInit(t, func() (string, error) {
		asked = true
		return jsonStub(t, 0, embedderLoadsJSON), nil
	})
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		return []lemonade.Model{{ID: "fireworks.deepseek-v4p1-flash", Recipe: "cloud"}}, nil
	})
	row := modelRow(localRunner{opts: LocalOptions{Model: "fireworks.deepseek-v4p1-flash"}})
	if !asked || row.State != StateOK || !strings.Contains(row.Line, "embedder") {
		t.Errorf("the Python agent's model check changed: asked=%v row=%+v", asked, row)
	}
}
