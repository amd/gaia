// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package voice

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

// Model is one speech model voice needs.
type Model struct {
	ID         string   `json:"id"`
	Purpose    string   `json:"purpose"`
	Downloaded bool     `json:"downloaded"`
	SizeGB     *float64 `json:"size_gb"`
}

// Microphone is the daemon machine's input device as the daemon sees it.
type Microphone struct {
	Available bool   `json:"available"`
	Name      string `json:"name"`
	Error     string `json:"error"`
	ErrorCode string `json:"error_code"`
}

// Status is GET /daemon/v1/voice/status: whether voice can start, and the
// settings a session runs with.
type Status struct {
	Ready         bool       `json:"ready"`
	Error         string     `json:"error"`
	ErrorCode     string     `json:"error_code"`
	Language      string     `json:"language"`
	ASRModel      string     `json:"asr_model"`
	TTSModel      string     `json:"tts_model"`
	Speaks        bool       `json:"speaks"`
	Models        []Model    `json:"models"`
	Microphone    Microphone `json:"microphone"`
	TurnTaking    string     `json:"turn_taking"`
	SilenceMS     int        `json:"silence_ms"`
	StatusUpdates bool       `json:"status_updates"`
	Captions      bool       `json:"captions"`
}

// HandsFree reports whether a pause ends the user's turn (else Space does).
func (s Status) HandsFree() bool { return s.TurnTaking != "push_to_talk" }

// Missing lists the models still to download.
func (s Status) Missing() []Model {
	var out []Model
	for _, m := range s.Models {
		if !m.Downloaded {
			out = append(out, m)
		}
	}
	return out
}

// Fetchers are package variables so tests can stand in a fake daemon.
var (
	FetchStatus   = fetchStatus
	DownloadModel = downloadModel
)

func fetchStatus(ctx context.Context) (Status, error) {
	body, err := daemon.CallContext(ctx, http.MethodGet, daemon.APIPrefix+"/voice/status",
		nil, true, "check whether voice can start", "")
	if err != nil {
		return Status{}, err
	}
	var st Status
	if err := json.Unmarshal(body, &st); err != nil {
		return Status{}, fmt.Errorf("the GAIA background service sent an unreadable voice status: %w", err)
	}
	return st, nil
}

func downloadModel(ctx context.Context, model string) error {
	payload, _ := json.Marshal(map[string]string{"model": model})
	_, err := daemon.CallContext(ctx, http.MethodPost, daemon.APIPrefix+"/voice/models/download",
		payload, true, "download the speech model "+model, "")
	return err
}

// ErrorText is the message a voice failure shows, keyed on its stable code.
// The daemon's own message is kept where it carries the specific remedy (which
// privacy pane, which package); generic codes get a fixed line.
func ErrorText(code, message string) string {
	switch code {
	case "mic_missing", "mic_busy", "mic_denied", "remote_lemonade":
		if message != "" {
			return message
		}
	}
	switch code {
	case "mic_missing":
		return "No microphone found on this PC."
	case "mic_busy":
		return "Your microphone is in use by another app."
	case "mic_denied":
		return "This PC refused GAIA access to the microphone. Allow it in your privacy settings."
	case "remote_lemonade":
		return "Voice only runs on a model server on this machine."
	case "lemonade_unreachable":
		return "Can't reach the local model server for speech yet. Try again in a moment."
	case "model_missing":
		if message != "" {
			return message
		}
		return "Voice needs a speech model that isn't downloaded."
	case "tts_failed":
		return "Couldn't speak this reply. It's on screen."
	case "asr_failed":
		return "Speech recognition failed. Try again."
	case "session_lost":
		return "Lost the connection to speech."
	}
	if message != "" {
		return message
	}
	return "Voice stopped: " + strings.ReplaceAll(code, "_", " ") + "."
}
