// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// SpeechModel is the Kokoro text-to-speech model Lemonade serves. Mirrors
// gaia.audio.lemonade_tts.DEFAULT_TTS_MODEL.
const SpeechModel = "kokoro-v1"

// speechTimeout bounds one synthesis request, like Python's
// DEFAULT_SYNTH_TIMEOUT. A pull has no timeout: only its context bounds it.
const speechTimeout = 120 * time.Second

// SpeechRequest is the body of POST /audio/speech.
type SpeechRequest struct {
	Model          string  `json:"model"`
	Input          string  `json:"input"`
	Voice          string  `json:"voice"`
	Speed          float64 `json:"speed"`
	ResponseFormat string  `json:"response_format"`
}

// SpeechModelState is what Lemonade's catalog says about the speech model.
type SpeechModelState struct {
	Downloaded bool
	SizeGB     float64
}

// speechCall sends one request for read-aloud. Like get, it accepts a remote
// server: speaking writes nothing on the server and sends no pasted key.
func (c *Client) speechCall(ctx context.Context, method, path string, data any, timeout time.Duration) (*http.Response, string, error) {
	base := c.current()
	u, err := url.Parse(base)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return nil, base, fmt.Errorf("the Lemonade address %q is not a valid http(s) URL", base)
	}
	var body io.Reader
	if data != nil {
		b, err := json.Marshal(data)
		if err != nil {
			return nil, base, fmt.Errorf("could not encode the request to Lemonade: %w", err)
		}
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, body)
	if err != nil {
		return nil, base, fmt.Errorf("the Lemonade address %q is not a valid URL", base)
	}
	if data != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	key, err := APIKeyFor(ctx, base)
	if err != nil {
		return nil, base, err
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	httpc := &http.Client{Timeout: timeout, CheckRedirect: c.HTTP.CheckRedirect}
	resp, err := httpc.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, base, ctx.Err()
		}
		return nil, base, fmt.Errorf("Lemonade Server is not reachable at %s (%v)", base, err)
	}
	return resp, base, nil
}

// SpeechStatus reads whether the speech model is on this server and
// downloaded. A server without it in its catalog is an error naming the
// text-to-speech models it does have.
func (c *Client) SpeechStatus(ctx context.Context) (SpeechModelState, error) {
	resp, base, err := c.speechCall(ctx, http.MethodGet, "/models?show_all=true", nil, 15*time.Second)
	if err != nil {
		return SpeechModelState{}, err
	}
	defer resp.Body.Close()
	if err := speechStatusError(resp, "the model list"); err != nil {
		return SpeechModelState{}, err
	}
	var reply struct {
		Data []struct {
			ID         string   `json:"id"`
			Downloaded bool     `json:"downloaded"`
			Labels     []string `json:"labels"`
			Size       float64  `json:"size"`
		} `json:"data"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&reply); err != nil {
		return SpeechModelState{}, fmt.Errorf("Lemonade at %s returned a model list GAIA could not read: %v", base, err)
	}
	var tts []string
	for _, m := range reply.Data {
		if m.ID == SpeechModel {
			return SpeechModelState{Downloaded: m.Downloaded, SizeGB: m.Size}, nil
		}
		for _, l := range m.Labels {
			if l == "tts" {
				tts = append(tts, m.ID)
			}
		}
	}
	have := "none"
	if len(tts) > 0 {
		have = strings.Join(tts, ", ")
	}
	return SpeechModelState{}, fmt.Errorf(
		"Lemonade at %s has no %s voice model (its text-to-speech models: %s). Update Lemonade Server to read replies aloud",
		base, SpeechModel, have)
}

// PullSpeechModel downloads the speech model. A built-in goes by name only:
// a recipe makes Lemonade treat it as a registration and refuse it (#1655).
func (c *Client) PullSpeechModel(ctx context.Context) error {
	resp, _, err := c.speechCall(ctx, http.MethodPost, "/pull", map[string]string{"model_name": SpeechModel}, 0)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if err := speechStatusError(resp, "the download of "+SpeechModel); err != nil {
		return err
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return nil
}

// Synthesize asks Lemonade to speak req and returns the audio bytes with
// their Content-Type.
func (c *Client) Synthesize(ctx context.Context, req SpeechRequest) ([]byte, string, error) {
	resp, _, err := c.speechCall(ctx, http.MethodPost, "/audio/speech", req, speechTimeout)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()
	if err := speechStatusError(resp, "speech"); err != nil {
		return nil, "", err
	}
	audio, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if err != nil {
		if ctx.Err() != nil {
			return nil, "", ctx.Err()
		}
		return nil, "", fmt.Errorf("Lemonade's speech reply was cut off: %v", err)
	}
	return audio, resp.Header.Get("Content-Type"), nil
}

// speechStatusError turns a failed reply into an error carrying the server's
// own message. A 401 body is never shown: some proxies echo the key into it.
func speechStatusError(resp *http.Response, what string) error {
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return fmt.Errorf("Lemonade rejected the API key (HTTP %d) on %s. Check LEMONADE_API_KEY", resp.StatusCode, what)
	}
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	return fmt.Errorf("Lemonade returned HTTP %d for %s: %s", resp.StatusCode, what, serverMessage(raw))
}

// serverMessage pulls the text out of Lemonade's {"error": {"message": ...}}
// envelope, else returns the body itself, trimmed. Mirrors Python's
// gaia.audio.lemonade_service.server_message.
func serverMessage(raw []byte) string {
	var env struct {
		Error json.RawMessage `json:"error"`
	}
	if json.Unmarshal(raw, &env) == nil && len(env.Error) > 0 {
		var inner struct {
			Message string `json:"message"`
		}
		if json.Unmarshal(env.Error, &inner) == nil && inner.Message != "" {
			return clip(inner.Message, 500)
		}
		var s string
		if json.Unmarshal(env.Error, &s) == nil && s != "" {
			return clip(s, 500)
		}
	}
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return "(empty body)"
	}
	return clip(text, 500)
}
