// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/speech"
)

// readAloudModel is a chat whose config.json is a throwaway file and whose
// audio player is play.
func readAloudModel(t *testing.T, config string, play speech.PlayFunc) (ChatModel, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	if config != "" {
		if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("GAIA_CONFIG_FILE", path)
	prev := readAloudDeps.findPlayer
	readAloudDeps.findPlayer = func() (speech.PlayFunc, error) { return play, nil }
	t.Cleanup(func() { readAloudDeps.findPlayer = prev })
	return slackModel(t), path
}

// drive runs cmd and every command its messages lead to, as Bubble Tea would,
// until nothing is left to run.
func drive(t *testing.T, m ChatModel, cmd tea.Cmd) ChatModel {
	t.Helper()
	for cmd != nil {
		got := make(chan tea.Msg, 1)
		go func(c tea.Cmd) { got <- c() }(cmd)
		var msg tea.Msg
		select {
		case msg = <-got:
		case <-time.After(10 * time.Second):
			t.Fatal("a read-aloud command never returned")
		}
		if msg == nil {
			return m
		}
		var next tea.Model
		next, cmd = m.Update(msg)
		m = next.(ChatModel)
	}
	return m
}

func withReply(m ChatModel, reply string) ChatModel {
	m.messages = append(m.messages, Message{Role: RoleAssistant, Content: reply})
	return m
}

func noPlay(context.Context, string) error { return nil }

// ttsServer serves the catalog, the pull and the speech endpoint, and
// records what was asked of it.
type ttsServer struct {
	mu         sync.Mutex
	downloaded bool
	pulls      []string
	speech     []map[string]any
	speechFail string
}

func (f *ttsServer) start(t *testing.T) string {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		raw, _ := io.ReadAll(r.Body)
		switch r.URL.Path {
		case "/api/v1/models":
			json.NewEncoder(w).Encode(map[string]any{"data": []map[string]any{
				{"id": "kokoro-v1", "downloaded": f.downloaded, "labels": []string{"tts"}, "size": 0.34},
			}})
		case "/api/v1/pull":
			f.pulls = append(f.pulls, string(raw))
			f.downloaded = true
			w.Write([]byte(`{"status":"success"}`))
		case "/api/v1/audio/speech":
			if f.speechFail != "" {
				w.WriteHeader(http.StatusInternalServerError)
				w.Write([]byte(`{"error":{"message":"` + f.speechFail + `"}}`))
				return
			}
			var body map[string]any
			json.Unmarshal(raw, &body)
			f.speech = append(f.speech, body)
			w.Header().Set("Content-Type", "audio/l16;rate=24000")
			w.Write([]byte{0, 0, 1, 0})
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(srv.Close)
	return srv.URL
}

func TestReadAloudAndVoiceRunLocallyNeverAsQuestions(t *testing.T) {
	m, _ := readAloudModel(t, "", noPlay)
	for _, q := range []string{"/read-aloud", "/read-aloud stop", "/voice", "/voice af_sky",
		"/voice speed 120", "/voice auto on", "/voice  auto   off", "/read-aloud nonsense"} {
		next, _ := m.submit(q)
		got := next.(ChatModel)
		if got.streaming {
			t.Errorf("%q started a turn", q)
		}
		for _, msg := range got.messages {
			if msg.Role == RoleUser {
				t.Errorf("%q was sent to the agent as a question", q)
			}
		}
	}
	if !isReadAloudCommand("/read-aloud stop") || isReadAloudCommand("/read-aloudly") ||
		!isVoiceCommand("/voice") || isVoiceCommand("/voices") {
		t.Error("command recognition is wrong")
	}
}

func TestVoiceRefusesBadValuesNamingWhatIsAllowed(t *testing.T) {
	m, path := readAloudModel(t, "", noPlay)
	for q, want := range map[string]string{
		"/voice xx_nobody":    "af_bella",
		"/voice speed 20":     "50 to 200",
		"/voice speed 201":    "50 to 200",
		"/voice speed fast":   "50 to 200",
		"/voice speed":        "50 to 200",
		"/voice auto maybe":   "/voice auto on",
		"/voice af_bella now": "/voice <id>",
		"/read-aloud loud":    "/read-aloud stop",
	} {
		next, cmd := m.submit(q)
		if cmd != nil {
			t.Errorf("%q saved something", q)
		}
		if got := lastError(t, next.(ChatModel)); !strings.Contains(got, want) {
			t.Errorf("%q: error %q does not name %q", q, got, want)
		}
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Error("a refused value was written to config.json")
	}
}

func TestVoiceSavesEachSettingAndConfirms(t *testing.T) {
	m, path := readAloudModel(t, `{"default_model": "keep-me"}`, noPlay)
	for q, want := range map[string]string{
		"/voice BM_George":  "Voice set to George (bm_george, British English, male).",
		"/voice speed 150%": "Read-aloud speed set to 150%.",
		"/voice auto on":    "Auto-read is on",
	} {
		next, cmd := m.submit(q)
		m = drive(t, next.(ChatModel), cmd)
		if got := lastStatus(t, m); !strings.Contains(got, want) {
			t.Errorf("%q confirmed %q, want %q", q, got, want)
		}
	}
	if !m.readAloudAuto {
		t.Error("/voice auto on did not turn auto-read on for this session")
	}
	raw, _ := os.ReadFile(path)
	var doc map[string]any
	json.Unmarshal(raw, &doc)
	if doc["read_aloud_voice"] != "bm_george" || doc["read_aloud_speed"] != float64(150) ||
		doc["read_aloud_auto"] != true || doc["default_model"] != "keep-me" {
		t.Fatalf("config.json = %v", doc)
	}
}

func TestVoiceShowsTheSettingsAndEveryVoiceGrouped(t *testing.T) {
	m, _ := readAloudModel(t, `{"read_aloud_voice": "bf_emma", "read_aloud_speed": 80}`, noPlay)
	next, cmd := m.submit("/voice")
	m = drive(t, next.(ChatModel), cmd)
	got := lastStatus(t, m)
	for _, want := range []string{
		"voice Emma (bf_emma, British English, female)", "speed 80%", "auto-read off",
		"American English, female: af_alloy", "American English, male: am_adam",
		"British English, female: bf_alice", "British English, male: bm_daniel",
		"bf_emma (current)",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("/voice does not show %q:\n%s", want, got)
		}
	}
}

func TestReadAloudPullsTheModelThenPlaysEveryPiece(t *testing.T) {
	lem := &ttsServer{}
	var mu sync.Mutex
	var played [][]byte
	m, _ := readAloudModel(t, `{"read_aloud_voice": "am_echo", "read_aloud_speed": 90}`,
		func(_ context.Context, path string) error {
			b, err := os.ReadFile(path)
			mu.Lock()
			played = append(played, b)
			mu.Unlock()
			return err
		})
	m.lemonadeBaseURL = lem.start(t)
	reply := "## Plan\n\n" + strings.Repeat("This sentence is part of a reply that is long enough to need pieces. ", 12)
	m = withReply(m, reply)

	next, cmd := m.submit("/read-aloud")
	m = drive(t, next.(ChatModel), cmd)

	for _, msg := range m.messages {
		if msg.Role == RoleError {
			t.Fatalf("reading failed: %s", msg.Content)
		}
	}
	if !strings.Contains(readAloudTranscript(m), "Downloading the kokoro-v1 voice model (~0.34 GB)") {
		t.Errorf("the download was not announced:\n%s", readAloudTranscript(m))
	}
	if len(lem.pulls) != 1 || lem.pulls[0] != `{"model_name":"kokoro-v1"}` {
		t.Errorf("pulls = %q", lem.pulls)
	}
	want := speech.Pieces(reply, speech.MaxPieceChars)
	if len(lem.speech) != len(want) || len(played) != len(want) {
		t.Fatalf("%d requests and %d plays for %d pieces", len(lem.speech), len(played), len(want))
	}
	for i, body := range lem.speech {
		if body["input"] != want[i] || body["voice"] != "am_echo" || body["speed"] != 0.9 ||
			body["model"] != "kokoro-v1" || body["response_format"] != "pcm" {
			t.Errorf("request %d = %v", i, body)
		}
		if string(played[i][:4]) != "RIFF" {
			t.Errorf("piece %d was not played as a WAV", i)
		}
	}
	if m.readAloud != nil {
		t.Error("a finished reading is still marked as playing")
	}
}

func readAloudTranscript(m ChatModel) string {
	var b strings.Builder
	for _, msg := range m.messages {
		b.WriteString(msg.Content + "\n")
	}
	return b.String()
}

func TestReadAloudFailureSaysWhatFailed(t *testing.T) {
	lem := &ttsServer{downloaded: true, speechFail: "voice am_echo is not loaded"}
	m, _ := readAloudModel(t, "", noPlay)
	m.lemonadeBaseURL = lem.start(t)
	m = withReply(m, "Hello there.")
	next, cmd := m.submit("/read-aloud")
	m = drive(t, next.(ChatModel), cmd)
	got := lastError(t, m)
	for _, want := range []string{"Could not read the reply aloud", "HTTP 500", "voice am_echo is not loaded"} {
		if !strings.Contains(got, want) {
			t.Errorf("error %q does not say %q", got, want)
		}
	}
}

func TestReadAloudAgainStopsThePlayer(t *testing.T) {
	lem := &ttsServer{downloaded: true}
	playing := make(chan struct{})
	killed := make(chan struct{})
	m, _ := readAloudModel(t, "", func(ctx context.Context, _ string) error {
		close(playing)
		<-ctx.Done()
		close(killed)
		return ctx.Err()
	})
	m.lemonadeBaseURL = lem.start(t)
	m = withReply(m, "Hello there.")
	next, cmd := m.submit("/read-aloud")
	m = next.(ChatModel)
	go func() { cmd() }()
	<-playing

	next, _ = m.submit("/read-aloud")
	m = next.(ChatModel)
	select {
	case <-killed:
	case <-time.After(5 * time.Second):
		t.Fatal("the player was not stopped")
	}
	if m.readAloud != nil || lastStatus(t, m) != "Stopped reading aloud." {
		t.Fatalf("not stopped: %q", lastStatus(t, m))
	}
	next, _ = m.submit("/read-aloud stop")
	if got := lastStatus(t, next.(ChatModel)); got != "Nothing is being read aloud." {
		t.Errorf("got %q", got)
	}
}

func TestReadAloudWithNothingToRead(t *testing.T) {
	m, _ := readAloudModel(t, "", noPlay)
	next, cmd := m.submit("/read-aloud")
	if cmd != nil || !strings.Contains(lastStatus(t, next.(ChatModel)), "no reply") {
		t.Errorf("got %q", lastStatus(t, next.(ChatModel)))
	}
	m = withReply(m, "```\nls -la\n```")
	next, cmd = m.submit("/read-aloud")
	if cmd != nil || !strings.Contains(lastStatus(t, next.(ChatModel)), "all code") {
		t.Errorf("got %q", lastStatus(t, next.(ChatModel)))
	}
}

func TestAutoReadReadsOnlyAFinishedReply(t *testing.T) {
	lem := &ttsServer{downloaded: true}
	m, _ := readAloudModel(t, "", noPlay)
	m.lemonadeBaseURL = lem.start(t)
	final := event.CanonicalFinalEvent{Type: "final", Answer: "All done."}

	m.streaming = true
	got, _, _ := m.handleCanonicalEvent(final)
	if got.readAloud != nil {
		t.Error("read a reply with auto-read off")
	}

	m.readAloudAuto = true
	m.streaming = true
	m.cancelPending = true
	got, _, _ = m.handleCanonicalEvent(final)
	if got.readAloud != nil {
		t.Error("read a reply the user stopped")
	}

	m.cancelPending = false
	m.streaming = true
	got, cmd, _ := m.handleCanonicalEvent(final)
	if got.readAloud == nil || cmd == nil {
		t.Fatal("auto-read did not read a finished reply")
	}
	got = drive(t, got, cmd)
	if len(lem.speech) != 1 || lem.speech[0]["input"] != "All done." {
		t.Errorf("speech requests = %v", lem.speech)
	}
}

func TestLaunchLoadsAutoRead(t *testing.T) {
	m, _ := readAloudModel(t, `{"read_aloud_auto": true}`, noPlay)
	next, _ := m.Update(loadReadAloudAutoCmd())
	if !next.(ChatModel).readAloudAuto {
		t.Error("read_aloud_auto: true was not loaded")
	}
}
