// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package lemonade

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
)

// Entry is one row of the model picker: a catalog model, or a local
// recommendation the catalog does not list yet (a custom model before its
// first pull, or a built-in an older Lemonade lacks).
type Entry struct {
	Model       Model
	Recommended *Recommended
	// Listed is false when the id came only from the recommendation list.
	Listed bool
	// Fits is meaningful for local models only; Reason says why not.
	Fits   bool
	Reason string
	// FitUnknown is set when the PC or the model's size could not be read,
	// so the row is blocked without having been judged too big.
	FitUnknown bool
	// NeedsUpgrade is set when this Lemonade is too old to load the model.
	NeedsUpgrade bool
}

// SizeGB is the download size, from the catalog or the recommendation.
func (e Entry) SizeGB() float64 {
	if e.Model.Size > 0 {
		return e.Model.Size
	}
	if e.Recommended != nil {
		return e.Recommended.SizeGB
	}
	return 0
}

// Selectable reports whether choosing the row can proceed: a downloaded local
// model, a local model that fits (it downloads first), or a listed cloud model.
func (e Entry) Selectable() bool {
	if e.Unavailable() {
		return false
	}
	if e.Model.Cloud() {
		return true
	}
	return e.Model.Downloaded || e.Fits
}

// Unavailable reports a recommended built-in this Lemonade does not offer, as
// opposed to one that does not fit this PC.
func (e Entry) Unavailable() bool {
	return !e.Listed && e.Recommended != nil && e.Recommended.RegisterAs == ""
}

// NeedsDownload reports whether choosing the row must pull the model first.
func (e Entry) NeedsDownload() bool {
	return !e.Model.Cloud() && !e.Model.Downloaded && e.Recommended.providerOr("local") == "local"
}

func (r *Recommended) providerOr(def string) string {
	if r == nil {
		return def
	}
	return r.Provider
}

func isChatModel(m Model) bool {
	if m.ID == "" || strings.HasPrefix(m.Recipe, "collection.") {
		return false
	}
	chat := false
	for _, label := range m.Labels {
		switch label {
		case "embeddings", "image", "reranker", "reranking", "audio", "tts", "stt", "transcription", "classification":
			return false
		case "chat":
			chat = true
		}
	}
	// Cloud listings do not always carry labels; their provider prefix is enough.
	return chat || m.Cloud()
}

// Catalog lists every chat model Lemonade offers for provider — downloaded or
// not — with the recommendations first and each local model judged against
// capacity. When capErr is set the machine could not be read, so no local
// download is allowed: a model that cannot be shown to fit must not be pulled.
func (c *Client) Catalog(ctx context.Context, provider string, capacity Capacity, capErr error) ([]Entry, error) {
	var reply struct {
		Data []Model `json:"data"`
	}
	if err := c.request(ctx, "GET", "/models?show_all=true", nil, &reply); err != nil {
		return nil, err
	}
	var models []Model
	for _, m := range reply.Data {
		if !isChatModel(m) {
			continue
		}
		if provider == "local" && !m.Cloud() {
			models = append(models, m)
		} else if provider != "local" && m.Cloud() && strings.HasPrefix(m.ID, provider+".") {
			models = append(models, m)
		}
	}
	return BuildEntries(provider, models, capacity, capErr), nil
}

// BuildEntries orders and judges a provider's catalog: recommendations first
// (including ones the catalog lacks), then the rest, selectable before not.
func BuildEntries(provider string, models []Model, capacity Capacity, capErr error) []Entry {
	judge := func(e *Entry) {
		if e.Model.Cloud() || e.Model.Downloaded {
			e.Fits = true
			return
		}
		if capErr != nil {
			e.FitUnknown = true
			e.Reason = "cannot check this PC's memory: " + capErr.Error()
			return
		}
		if e.Recommended != nil {
			if ok, why := capacity.SupportsModel(e.Recommended.MinLemonade); !ok {
				e.NeedsUpgrade = true
				e.Reason = why
				return
			}
		}
		size := e.SizeGB()
		if size <= 0 {
			e.FitUnknown = true
			e.Reason = "Lemonade did not report its size, so GAIA cannot check it fits"
			return
		}
		e.Fits, e.Reason = capacity.Fit(size)
	}

	used := make([]bool, len(models))
	var head []Entry
	for _, rec := range RecommendedFor(provider) {
		rec := rec
		matched := false
		for i, m := range models {
			if !used[i] && rec.Matches(m.ID) {
				used[i], matched = true, true
				e := Entry{Model: m, Recommended: &rec, Listed: true}
				judge(&e)
				head = append(head, e)
			}
		}
		// A cloud recommendation the account does not list is simply absent.
		if matched || provider != "local" {
			continue
		}
		e := Entry{Model: Model{ID: rec.ID}, Recommended: &rec}
		switch {
		case rec.RegisterAs == "":
			// A built-in this Lemonade does not ship: it cannot be pulled by name.
			e.Reason = "this Lemonade server does not offer it; update Lemonade with `gaia init`"
		default:
			judge(&e)
		}
		head = append(head, e)
	}
	var rest []Entry
	for i, m := range models {
		if used[i] {
			continue
		}
		e := Entry{Model: m, Listed: true}
		judge(&e)
		rest = append(rest, e)
	}
	sort.SliceStable(rest, func(i, j int) bool {
		if rest[i].Selectable() != rest[j].Selectable() {
			return rest[i].Selectable()
		}
		if rest[i].Model.Downloaded != rest[j].Model.Downloaded {
			return rest[i].Model.Downloaded
		}
		return strings.ToLower(rest[i].Model.ID) < strings.ToLower(rest[j].Model.ID)
	})
	return append(head, rest...)
}

// PullProgress is one progress report from a streamed download.
type PullProgress struct {
	File       string  `json:"file"`
	FileIndex  int     `json:"file_index"`
	TotalFiles int     `json:"total_files"`
	Downloaded int64   `json:"bytes_downloaded"`
	Total      int64   `json:"bytes_total"`
	Percent    float64 `json:"percent"`
}

// PullRequest builds the /pull body for an entry. Built-ins go by name only —
// sending a recipe for one makes Lemonade treat it as a registration and 400
// (#1655); a custom model carries its registration fields.
func PullRequest(e Entry) map[string]any {
	body := map[string]any{"model_name": e.Model.ID, "stream": true}
	r := e.Recommended
	if r == nil || r.RegisterAs == "" {
		return body
	}
	body["model_name"] = r.RegisterAs
	body["checkpoint"] = r.Checkpoint
	body["recipe"] = r.Recipe
	if r.MMProj != "" {
		body["mmproj"] = r.MMProj
	}
	if r.Vision {
		body["vision"] = true
	}
	if r.Reasoning {
		body["reasoning"] = true
	}
	return body
}

// Pull downloads (and for a custom model, registers) an entry, reporting
// progress. It refuses an entry that does not fit: the picker should never
// offer one, and this is the last line that keeps it from happening anyway.
func (c *Client) Pull(ctx context.Context, e Entry, progress func(PullProgress)) error {
	if !e.NeedsDownload() {
		return fmt.Errorf("%s does not need a download", e.Model.ID)
	}
	if !e.Fits {
		return fmt.Errorf("%s will not fit this PC: %s", e.Model.ID, e.Reason)
	}
	if err := validateURL(c.BaseURL, true); err != nil {
		return err
	}
	b, err := json.Marshal(PullRequest(e))
	if err != nil {
		return fmt.Errorf("could not encode the download request")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/pull", bytes.NewReader(b))
	if err != nil {
		return fmt.Errorf("invalid Lemonade address")
	}
	req.Header.Set("Content-Type", "application/json")
	if key := APIKeyFor(c.BaseURL); key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	// A download runs for as long as it takes; ctx is the only bound.
	httpc := &http.Client{CheckRedirect: c.HTTP.CheckRedirect}
	resp, err := httpc.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("Lemonade did not respond to the download request. Check it is running and retry")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("Lemonade refused to download %s (HTTP %d). Check its log, then retry", e.Model.ID, resp.StatusCode)
	}
	return readPullStream(resp.Body, e.Model.ID, progress)
}

func readPullStream(body interface{ Read([]byte) (int, error) }, id string, progress func(PullProgress)) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	event := ""
	for scanner.Scan() {
		line := scanner.Text()
		switch {
		case strings.HasPrefix(line, "event:"):
			event = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
		case strings.HasPrefix(line, "data:"):
			data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			switch event {
			case "complete":
				return nil
			case "error":
				var e struct {
					Error string `json:"error"`
				}
				_ = json.Unmarshal([]byte(data), &e)
				if e.Error == "" {
					e.Error = data
				}
				return fmt.Errorf("download of %s failed: %s", id, e.Error)
			default:
				var p PullProgress
				if json.Unmarshal([]byte(data), &p) == nil && progress != nil {
					progress(p)
				}
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("download of %s was interrupted: %v", id, err)
	}
	return fmt.Errorf("download of %s ended without Lemonade confirming it finished. Retry to resume", id)
}
