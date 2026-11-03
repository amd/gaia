// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package setupapi

import (
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
