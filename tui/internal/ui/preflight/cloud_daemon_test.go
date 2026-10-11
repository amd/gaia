package preflight

import (
	"context"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/lemonade"
)

// Once setup installs the hub flagship, the TUI reaches it through the daemon,
// whose readiness answer names the agent's default LOCAL chat model. A launch on
// a cloud model was blocked behind that ~4 GB download it never uses.
func cloudDaemonCfg(model string) Config {
	return Config{AgentID: "email", AgentName: "Email", Model: model}
}

func TestDaemonGatePassesACloudModelWithoutTheLocalChatModel(t *testing.T) {
	stubListedCloudModels(t, func(_ context.Context, provider string) ([]lemonade.Model, error) {
		if provider != "amd" {
			t.Errorf("asked for provider %q, want amd", provider)
		}
		return []lemonade.Model{{ID: "amd.deepseek-v4.1-flash", Recipe: "cloud"}}, nil
	})
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)

	rep := Check(context.Background(), f, cloudDaemonCfg("amd.DeepSeek-V4.1-Flash"))

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
	f := newFake().with("GET /v1/email/init", 200, initReady)

	rep := Check(context.Background(), f, cloudDaemonCfg("amd.DeepSeek-V4.1-Flash"))

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
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)

	rep := Check(context.Background(), f, cloudDaemonCfg("amd.Gemma-4-31B"))

	if row, _ := rep.Find(KeyModel); row.State != StateOK || !replayed {
		t.Fatalf("the kept key was not handed back before refusing: replayed=%v row=%+v", replayed, row)
	}
}

func TestDaemonGateStillRequiresTheLocalModelForALocalLaunch(t *testing.T) {
	stubListedCloudModels(t, func(context.Context, string) ([]lemonade.Model, error) {
		t.Error("a local launch must not ask a cloud provider anything")
		return nil, nil
	})
	f := newFake().with("GET /v1/email/init", 503, initModelMissing)

	rep := Check(context.Background(), f, cloudDaemonCfg(""))

	blocker, blocked := rep.Blocker()
	if !blocked || blocker.Key != KeyModel || blocker.Fix != FixPullModel {
		t.Fatalf("a missing local chat model no longer blocks a local launch: %+v", rep.Rows)
	}
}
