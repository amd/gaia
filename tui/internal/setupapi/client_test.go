// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package setupapi

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/amd/gaia/tui/internal/daemon"
)

func TestDecodeReadsAPlan(t *testing.T) {
	var p Plan
	ack, err := decode("/daemon/v1/setup/plan", "read", 200,
		[]byte(`{"chat_model":{"id":"Gemma-4-E4B-it-GGUF"},"catalog":[{"id":"x","fits":null,"size_gb":0.33}]}`), &p)
	if err != nil || ack != nil {
		t.Fatalf("ack=%v err=%v", ack, err)
	}
	if p.ChatModel.ID != "Gemma-4-E4B-it-GGUF" || len(p.Catalog) != 1 {
		t.Fatalf("plan = %+v", p)
	}
	if p.Catalog[0].Fits != nil {
		t.Error("a null fit must stay unknown, not become false")
	}
}

func TestDecodeAcceptsAccepted(t *testing.T) {
	if _, err := decode("/daemon/v1/setup/models/download", "download", 202, []byte(`{"state":"idle"}`), nil); err != nil {
		t.Fatal(err)
	}
}

// A 409 with a structured detail is a question for the user, not an error.
func TestDecodeTurnsAConfirmationIntoAnAck(t *testing.T) {
	body := `{"detail":{"code":"no_fit","message":"Qwen3.6-35B-A3B-GGUF may not run on this PC","need_gb":26.8,"capacity_gb":16.0,"acknowledge":"acknowledge_no_fit"}}`
	ack, err := decode("/daemon/v1/setup/chat-model", "choose", 409, []byte(body), nil)
	if err != nil || ack == nil {
		t.Fatalf("ack=%v err=%v", ack, err)
	}
	if ack.Code != AckNoFit || *ack.NeedGB != 26.8 || *ack.CapacityGB != 16 {
		t.Fatalf("ack = %+v", ack)
	}
}

// A plain-string 409 ("already running") stays an error with its own words.
func TestDecodeKeepsAPlain409AsAnError(t *testing.T) {
	_, err := decode("/daemon/v1/setup/run", "run", 409, []byte(`{"detail":"Setup is already running."}`), nil)
	if err == nil || err.Error() != "Setup is already running." || !IsStatus(err, 409) {
		t.Fatalf("err = %v", err)
	}
}

func TestDecodeNamesAnOlderDaemon(t *testing.T) {
	_, err := decode("/daemon/v1/setup/plan", "read setup's model plan", 404, []byte(`{"detail":"Not Found"}`), nil)
	var missing *daemon.RouteMissingError
	if !errors.As(err, &missing) {
		t.Fatalf("err = %v, want a route-missing error", err)
	}
	if !strings.Contains(err.Error(), "gaia daemon restart") {
		t.Errorf("the remedy is missing: %v", err)
	}
}

func TestDecodeRefusalCarriesTheBackendsWords(t *testing.T) {
	_, err := decode("/daemon/v1/gpu-memory/apply", "change", 403, []byte(`{"detail":"Permission wasn't given."}`), nil)
	if !IsStatus(err, 403) || !strings.Contains(err.Error(), "Permission") {
		t.Fatalf("err = %v", err)
	}
	_, err = decode("/daemon/v1/setup/plan", "read", 500, []byte(`oops`), nil)
	if !IsStatus(err, 500) || err.Error() != "oops" {
		t.Fatalf("err = %v", err)
	}
	var p Plan
	if _, err = decode("/daemon/v1/setup/plan", "read", 200, []byte(`not json`), &p); err == nil {
		t.Fatal("unreadable JSON must be an error, not an empty plan")
	}
}

// The plan's new fields, in the shape setup_plan.py sends them, nulls included.
func TestDecodeReadsTheJobFieldsOfAPlan(t *testing.T) {
	body := `{
		"chat_model": {"id": "Gemma-4-E4B-it-GGUF"},
		"vision_model": null,
		"components": [
			{"id": "server", "version": "10.1", "size_gb": 0.007, "source": "github.com/x", "installed": true},
			{"id": "packages", "version": null, "size_gb": null, "source": "pypi.org", "installed": null}
		],
		"backends": [{"recipe": "llamacpp", "backend": "vulkan", "state": "installed", "default": true}],
		"catalog": [
			{"id": "m", "support": "verified", "reads_images": true, "feature_tier": "compact", "gaia_model": true, "source": "huggingface.co/x/y"},
			{"id": "w", "support": null, "reads_images": false, "feature_tier": "full", "gaia_model": true, "source": null, "roles": ["speech"]}
		]
	}`
	var p Plan
	if _, err := decode("/daemon/v1/setup/plan", "read", 200, []byte(body), &p); err != nil {
		t.Fatal(err)
	}
	if p.VisionModel != nil {
		t.Errorf("vision_model = %v, want nil", *p.VisionModel)
	}
	if !p.Components[0].IsInstalled() || p.Components[1].IsInstalled() || p.Components[1].SizeGB != nil {
		t.Errorf("components = %+v", p.Components)
	}
	if len(p.Backends) != 1 || !p.Backends[0].Default {
		t.Errorf("backends = %+v", p.Backends)
	}
	m, w := p.Catalog[0], p.Catalog[1]
	if *m.Support != SupportVerified || !m.ReadsImages || m.FeatureTier != TierCompact || !m.GaiaModel || *m.Source != "huggingface.co/x/y" {
		t.Errorf("model = %+v", m)
	}
	if w.Support != nil || w.Source != nil || !w.HasRole(RoleSpeech) {
		t.Errorf("optional model = %+v", w)
	}
}

// The status's new fields, in the shape setup_runner.py sends them. An idle
// backend sends neither.
func TestDecodeReadsComponentsAndTestRows(t *testing.T) {
	body := `{"state": "failed", "components": {"server": "done", "agent": "failed"}, "tests": [
		{"job": "server", "subject": null, "state": "passed", "error": null, "kind": null, "seconds": null},
		{"job": "chat", "subject": "m", "state": "failed", "error": "would not load", "kind": "broken", "seconds": 3.2},
		{"job": "speech", "subject": "w", "state": "failed", "error": "no", "kind": "stuck", "seconds": 1}
	]}`
	var s Status
	if _, err := decode("/daemon/v1/setup/status", "read", 200, []byte(body), &s); err != nil {
		t.Fatal(err)
	}
	if s.Components["server"] != ComponentDone || s.Components["agent"] != ComponentFailed {
		t.Errorf("components = %v", s.Components)
	}
	if len(s.Tests) != 3 || s.Tests[0].Subject != nil || *s.Tests[1].Kind != KindBroken || *s.Tests[1].Seconds != 3.2 {
		t.Errorf("tests = %+v", s.Tests)
	}
	if s.Tests[1].Optional() || !s.Tests[2].Optional() {
		t.Error("only speech, voice and image creation are optional")
	}
	var idle Status
	if _, err := decode("/daemon/v1/setup/status", "read", 200, []byte(`{"state":"idle"}`), &idle); err != nil {
		t.Fatal(err)
	}
	if len(idle.Tests) != 0 || idle.Components["server"] != "" {
		t.Errorf("idle = %+v", idle)
	}
}

// The backend's extra_models is a list, so a Go nil must not reach it as null.
func TestARunWithoutExtrasOmitsTheField(t *testing.T) {
	raw, err := json.Marshal(RunRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "extra_models") {
		t.Fatalf("body = %s", raw)
	}
	raw, _ = json.Marshal(RunRequest{ExtraModels: []string{"m"}})
	if !strings.Contains(string(raw), `"extra_models":["m"]`) {
		t.Fatalf("body = %s", raw)
	}
}
