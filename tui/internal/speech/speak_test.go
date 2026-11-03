// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package speech

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/amd/gaia/tui/internal/lemonade"
)

func TestSpeakPlaysPiecesInOrderAndRemovesTheFiles(t *testing.T) {
	var played []string
	var paths []string
	synth := func(_ context.Context, text string) ([]byte, error) { return []byte(text), nil }
	play := func(_ context.Context, path string) error {
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		played = append(played, string(b))
		paths = append(paths, path)
		return nil
	}
	if err := Speak(context.Background(), []string{"one", "two", "three"}, synth, play); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(played, []string{"one", "two", "three"}) {
		t.Fatalf("played %q", played)
	}
	for _, p := range paths {
		if _, err := os.Stat(p); !os.IsNotExist(err) {
			t.Errorf("%s was left behind", p)
		}
	}
}

func TestSpeakFetchesTheNextPieceWhileOnePlays(t *testing.T) {
	secondRequested := make(chan struct{})
	synth := func(_ context.Context, text string) ([]byte, error) {
		if text == "two" {
			close(secondRequested)
		}
		return []byte(text), nil
	}
	first := true
	play := func(_ context.Context, _ string) error {
		if first {
			first = false
			select {
			case <-secondRequested:
			case <-time.After(5 * time.Second):
				return errors.New("the second piece was not requested while the first played")
			}
		}
		return nil
	}
	if err := Speak(context.Background(), []string{"one", "two"}, synth, play); err != nil {
		t.Fatal(err)
	}
}

func TestSpeakStopsOnTheFirstFailure(t *testing.T) {
	boom := errors.New("Lemonade returned HTTP 500 for speech: no such voice")
	var played int
	synth := func(_ context.Context, text string) ([]byte, error) {
		if text == "two" {
			return nil, boom
		}
		return []byte(text), nil
	}
	play := func(context.Context, string) error { played++; return nil }
	err := Speak(context.Background(), []string{"one", "two", "three"}, synth, play)
	if !errors.Is(err, boom) {
		t.Fatalf("got %v", err)
	}
	if played != 1 {
		t.Fatalf("played %d pieces, want 1", played)
	}
}

func TestStoppingMidPlayKillsPlaybackAndCleansUp(t *testing.T) {
	dir := t.TempDir()
	tempDir = dir
	defer func() { tempDir = "" }()
	ctx, cancel := context.WithCancel(context.Background())
	var mu sync.Mutex
	var written []string
	synth := func(_ context.Context, text string) ([]byte, error) { return []byte(text), nil }
	play := func(ctx context.Context, path string) error {
		mu.Lock()
		written = append(written, path)
		mu.Unlock()
		cancel()
		<-ctx.Done()
		return ctx.Err()
	}
	err := Speak(ctx, []string{"one", "two"}, synth, play)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	// The prefetched second file is removed by a goroutine once it lands.
	deadline := time.Now().Add(5 * time.Second)
	for {
		matches, _ := os.ReadDir(dir)
		if len(matches) == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("temporary audio left behind: %v", matches)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestLemonadeSynthesizerSendsTheSpeechRequestAndWrapsThePCM(t *testing.T) {
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/v1/audio/speech" {
			http.Error(w, "unexpected "+r.Method+" "+r.URL.Path, http.StatusNotFound)
			return
		}
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, &got); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "audio/l16;rate=22050;channels=1")
		w.Write([]byte{1, 0, 2, 0})
	}))
	defer srv.Close()

	synth := LemonadeSynthesizer(lemonade.New(srv.URL), Settings{Voice: "bm_george", Speed: 125})
	wav, err := synth(context.Background(), "Hello there.")
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"model": "kokoro-v1", "input": "Hello there.", "voice": "bm_george",
		"speed": 1.25, "response_format": "pcm",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("request body %v\nwant %v", got, want)
	}
	if string(wav[:4]) != "RIFF" || len(wav) != 48 {
		t.Fatalf("not a WAV around the 4 PCM bytes: %v", wav)
	}
	if rate := binary.LittleEndian.Uint32(wav[24:]); rate != 22050 {
		t.Fatalf("WAV rate %d, want the 22050 Lemonade named", rate)
	}
}

func TestLemonadeSynthesizerSurfacesTheServersError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":{"message":"Unknown voice 'xx_nobody'"}}`))
	}))
	defer srv.Close()
	_, err := LemonadeSynthesizer(lemonade.New(srv.URL), Settings{Voice: "af_bella", Speed: 100})(context.Background(), "Hi.")
	if err == nil || !strings.Contains(err.Error(), "HTTP 500") || !strings.Contains(err.Error(), "Unknown voice 'xx_nobody'") {
		t.Fatalf("got %v", err)
	}
}
