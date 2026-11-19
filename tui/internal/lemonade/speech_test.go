// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

func speechServer(t *testing.T, h http.HandlerFunc) *Client {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return New(srv.URL)
}

func TestSpeechStatusReadsTheVoiceModelFromTheCatalog(t *testing.T) {
	c := speechServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/models" || r.URL.Query().Get("show_all") != "true" {
			http.NotFound(w, r)
			return
		}
		w.Write([]byte(`{"data":[{"id":"Gemma-4-E4B-it-GGUF","downloaded":true,"labels":["chat"]},` +
			`{"id":"kokoro-v1","downloaded":false,"labels":["tts"],"size":0.34}]}`))
	})
	st, err := c.SpeechStatus(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if st.Downloaded || st.SizeGB != 0.34 {
		t.Fatalf("got %+v", st)
	}
}

func TestSpeechStatusWithoutTheModelNamesWhatTheServerHas(t *testing.T) {
	c := speechServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"data":[{"id":"other-tts","labels":["tts"]}]}`))
	})
	_, err := c.SpeechStatus(context.Background())
	if err == nil || !strings.Contains(err.Error(), "no kokoro-v1") || !strings.Contains(err.Error(), "other-tts") {
		t.Fatalf("got %v", err)
	}
}

func TestPullSpeechModelGoesByNameOnly(t *testing.T) {
	var body map[string]any
	c := speechServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/pull" {
			http.NotFound(w, r)
			return
		}
		raw, _ := io.ReadAll(r.Body)
		json.Unmarshal(raw, &body)
		w.Write([]byte(`{"status":"success"}`))
	})
	if err := c.PullSpeechModel(context.Background()); err != nil {
		t.Fatal(err)
	}
	// A recipe on a built-in makes Lemonade 400 (#1655).
	if want := map[string]any{"model_name": "kokoro-v1"}; !reflect.DeepEqual(body, want) {
		t.Fatalf("pull body %v, want %v", body, want)
	}
}

func TestSpeechSendsTheAPIKey(t *testing.T) {
	t.Setenv("LEMONADE_API_KEY", "test-key")
	var auth string
	c := speechServer(t, func(w http.ResponseWriter, r *http.Request) {
		auth = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "audio/l16;rate=24000")
		w.Write([]byte{0, 0})
	})
	if _, _, err := c.Synthesize(context.Background(), SpeechRequest{Model: SpeechModel, Input: "hi"}); err != nil {
		t.Fatal(err)
	}
	if auth != "Bearer test-key" {
		t.Fatalf("Authorization = %q", auth)
	}
}

func TestSpeechErrorsSayWhatFailed(t *testing.T) {
	c := speechServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte("Bearer secret-echoed-by-a-proxy"))
	})
	_, _, err := c.Synthesize(context.Background(), SpeechRequest{Model: SpeechModel, Input: "hi"})
	if err == nil || !strings.Contains(err.Error(), "rejected the API key") || strings.Contains(err.Error(), "secret") {
		t.Fatalf("got %v", err)
	}

	srv := httptest.NewServer(http.NotFoundHandler())
	base := srv.URL
	srv.Close()
	_, _, err = New(base).Synthesize(context.Background(), SpeechRequest{Model: SpeechModel, Input: "hi"})
	if err == nil || !strings.Contains(err.Error(), "not reachable at "+base+"/api/v1") {
		t.Fatalf("got %v", err)
	}
}
