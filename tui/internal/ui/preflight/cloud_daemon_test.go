package preflight

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// Once setup installs the hub flagship, the TUI reaches it through the daemon,
// whose readiness answer names the agent's default LOCAL chat model. A launch on
// a cloud model was blocked behind that ~4 GB download it never uses.
func cloudDaemonCfg(model string) Config {
	return Config{AgentID: "gaia", AgentName: "GAIA", Model: model}
}

// gaiaFake is newFake's daemon answering for the flagship instead of email.
func gaiaFake(initStatus int, initBody string) *fakeTransport {
	f := newFake()
	f.bodies = map[string]Response{
		"GET /daemon/v1/agents": {Status: 200, Body: []byte(strings.ReplaceAll(
			agentsRunning, `"agent_id":"email"`, `"agent_id":"gaia"`))},
		"GET /v1/gaia/health": {Status: 200, Body: []byte(healthOK)},
		"GET /v1/gaia/init":   {Status: initStatus, Body: []byte(initBody)},
	}
	return f
}

func TestDaemonGatePassesACloudModelWithoutTheLocalChatModel(t *testing.T) {
	stubListedCloudModels(t, func(_ context.Context, provider string) ([]lemonade.Model, error) {
		if provider != "amd" {
			t.Errorf("asked for provider %q, want amd", provider)
		}
		return []lemonade.Model{{ID: "amd.deepseek-v4.1-flash", Recipe: "cloud"}}, nil
	})

	rep := Check(context.Background(), gaiaFake(503, initModelMissing),
		cloudDaemonCfg("amd.DeepSeek-V4.1-Flash"))

	row, _ := rep.Find(KeyModel)
	if row.State != StateOK {
		t.Fatalf("a listed cloud model was blocked on the local chat model: %+v", row)
	}
	if strings.Contains(row.Line, "Gemma") || !strings.Contains(row.Line, "amd.DeepSeek-V4.1-Flash") {
		t.Errorf("model row %q should name the cloud model, not the local default", row.Line)
	}
	if blocker, blocked := rep.Blocker(); blocked {
		t.Fatalf("launch refused by %s: %+v", blocker.Key, blocker)
	}
}

func TestDaemonGateStopsACloudModelItsServerCannotServe(t *testing.T) {
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		return nil, nil
	})

	rep := Check(context.Background(), gaiaFake(200, initReady),
		cloudDaemonCfg("amd.DeepSeek-V4.1-Flash"))

	blocker, blocked := rep.Blocker()
	if !blocked || blocker.Key != KeyModel {
		t.Fatalf("an unservable cloud model passed the gate: %+v", rep.Rows)
	}
	if !strings.Contains(blocker.Remedy.Action, "Press p") {
		t.Errorf("remedy %q does not say how to reconnect", blocker.Remedy.Action)
	}
}

func TestDaemonGateReplaysTheKeptKeyForACloudModel(t *testing.T) {
	replayed := false
	stubReplayKeptKey(t, func(_ context.Context, provider string) (bool, error) {
		replayed = provider == "amd"
		return replayed, nil
	})
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		if !replayed {
			return nil, nil
		}
		return []lemonade.Model{{ID: "amd.Gemma-4-31B", Recipe: "cloud"}}, nil
	})

	rep := Check(context.Background(), gaiaFake(503, initModelMissing),
		cloudDaemonCfg("amd.Gemma-4-31B"))

	if row, _ := rep.Find(KeyModel); row.State != StateOK || !replayed {
		t.Fatalf("the kept key was not handed back before refusing: replayed=%v row=%+v", replayed, row)
	}
}

// With a gateway model saved as default_model the launch passes no model, so
// the sidecar names it — and a restarted Lemonade, keyless, lists it as missing.
// It was offered as a ~4 GB download instead of having its key handed back.
func TestDaemonGateTreatsACloudDefaultModelAsCloud(t *testing.T) {
	replayed := false
	stubReplayKeptKey(t, func(_ context.Context, provider string) (bool, error) {
		replayed = provider == "amd"
		return replayed, nil
	})
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		if !replayed {
			return nil, nil
		}
		return []lemonade.Model{{ID: "amd.DeepSeek-V4.1-Flash", Recipe: "cloud"}}, nil
	})
	missing := strings.ReplaceAll(initModelMissing, "Gemma-4-E4B-it-GGUF", "amd.DeepSeek-V4.1-Flash")

	rep := Check(context.Background(), gaiaFake(503, missing), cloudDaemonCfg(""))

	row, _ := rep.Find(KeyModel)
	if row.State != StateOK || !replayed {
		t.Fatalf("a cloud default model was not checked as cloud: replayed=%v row=%+v", replayed, row)
	}
	if row.Fix == FixPullModel || strings.Contains(row.Detail, "4 GB") {
		t.Errorf("a cloud model was described as a download: %+v", row)
	}
}

// cloudDefault is the sidecar's /init answer when default_model is a gateway
// model and a restarted, keyless Lemonade lists no amd.* models.
func cloudDefault(id string) string {
	return strings.ReplaceAll(initModelMissing, "Gemma-4-E4B-it-GGUF", id)
}

// No kept key, a failed replay, or a model the provider does not list: each
// must ask for the provider, never offer the gateway model as a download.
func TestDaemonGateNeverOffersACloudDefaultAsADownload(t *testing.T) {
	cases := map[string]func(context.Context, string) (bool, error){
		"no kept key":      func(context.Context, string) (bool, error) { return false, nil },
		"replay failed":    func(context.Context, string) (bool, error) { return false, errors.New("daemon unreachable") },
		"model not listed": func(context.Context, string) (bool, error) { return true, nil },
	}
	for name, replay := range cases {
		t.Run(name, func(t *testing.T) {
			stubReplayKeptKey(t, replay)
			stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
				return nil, nil
			})

			rep := Check(context.Background(), gaiaFake(503, cloudDefault("amd.DeepSeek-V4.1-Flash")), cloudDaemonCfg(""))

			blocker, blocked := rep.Blocker()
			if !blocked || blocker.Key != KeyModel {
				t.Fatalf("an unservable cloud default passed the gate: %+v", rep.Rows)
			}
			if blocker.Fix == FixPullModel || strings.Contains(blocker.Detail, "4 GB") ||
				strings.Contains(blocker.Line, "not downloaded") {
				t.Errorf("a cloud default was described as a download: %+v", blocker)
			}
			if !strings.Contains(blocker.Remedy.Action, "Press p") {
				t.Errorf("remedy %q does not say how to reconnect", blocker.Remedy.Action)
			}
		})
	}
}

// A model passed for this launch outranks the sidecar's saved default.
func TestDaemonGateChecksTheLaunchCloudModelOverACloudDefault(t *testing.T) {
	var asked []string
	stubListedCloudModels(t, func(_ context.Context, provider string) ([]lemonade.Model, error) {
		asked = append(asked, provider)
		return []lemonade.Model{{ID: "fireworks.kimi-k2", Recipe: "cloud"}}, nil
	})

	rep := Check(context.Background(), gaiaFake(503, cloudDefault("amd.DeepSeek-V4.1-Flash")),
		cloudDaemonCfg("fireworks.kimi-k2"))

	row, _ := rep.Find(KeyModel)
	if row.State != StateOK || row.Line != "chat uses fireworks.kimi-k2" {
		t.Fatalf("the launch's cloud model was not the one checked: %+v", row)
	}
	if strings.Join(asked, ",") != "fireworks" {
		t.Errorf("asked providers %v, want only fireworks", asked)
	}
}

// Only a provider-prefixed id is cloud: a local model whose name merely starts
// with "amd" keeps its download gate.
func TestDaemonGateKeepsTheDownloadForALocalLookalikeDefault(t *testing.T) {
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		t.Error("a local default must not ask a cloud provider anything")
		return nil, nil
	})
	for _, id := range []string{"amdgpu-Qwen3-4B-GGUF", "AMD-Llama-3B-GGUF", "fireworks-mini-GGUF"} {
		rep := Check(context.Background(), gaiaFake(503, cloudDefault(id)), cloudDaemonCfg(""))
		if blocker, blocked := rep.Blocker(); !blocked || blocker.Fix != FixPullModel {
			t.Errorf("%s lost its download gate: %+v", id, rep.Rows)
		}
	}
}

func TestDaemonGateStillRequiresTheLocalModelForALocalLaunch(t *testing.T) {
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		t.Error("a local launch must not ask a cloud provider anything")
		return nil, nil
	})

	rep := Check(context.Background(), gaiaFake(503, initModelMissing), cloudDaemonCfg(""))

	blocker, blocked := rep.Blocker()
	if !blocked || blocker.Key != KeyModel || blocker.Fix != FixPullModel {
		t.Fatalf("a missing local chat model no longer blocks a local launch: %+v", rep.Rows)
	}
}

// A saved gateway default_model is the sidecar's model even when this launch
// picked a local one; it must not come back as a ~4 GB download.
func TestDaemonGateChecksThePickedLocalModelOverACloudDefault(t *testing.T) {
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		t.Error("a local launch must not ask a cloud provider anything")
		return nil, nil
	})
	orig := downloadedLocalModels
	t.Cleanup(func() { downloadedLocalModels = orig })
	downloadedLocalModels = func(context.Context) ([]lemonade.Model, error) {
		return []lemonade.Model{{ID: "Gemma-4-E4B-it-GGUF", Downloaded: true}}, nil
	}
	cloudDefault := strings.ReplaceAll(initModelMissing, "Gemma-4-E4B-it-GGUF", "amd.DeepSeek-V4.1-Flash")

	rep := Check(context.Background(), gaiaFake(503, cloudDefault), cloudDaemonCfg("Gemma-4-E4B-it-GGUF"))

	row, _ := rep.Find(KeyModel)
	if row.State != StateOK || strings.Contains(row.Line, "amd.") || strings.Contains(row.Detail, "4 GB") {
		t.Fatalf("the cloud default was reported instead of the picked local model: %+v", row)
	}
	if blocker, blocked := rep.Blocker(); blocked {
		t.Fatalf("launch refused by %s: %+v", blocker.Key, blocker)
	}

	downloadedLocalModels = func(context.Context) ([]lemonade.Model, error) { return nil, nil }
	rep = Check(context.Background(), gaiaFake(503, cloudDefault), cloudDaemonCfg("Gemma-4-E4B-it-GGUF"))
	blocker, blocked := rep.Blocker()
	if !blocked || blocker.Key != KeyModel || !strings.Contains(blocker.Line, "Gemma-4-E4B-it-GGUF") ||
		strings.Contains(blocker.Detail, "4 GB") {
		t.Fatalf("a missing picked model was not reported by name: %+v", rep.Rows)
	}
}
