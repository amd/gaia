// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package setupapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/amd/gaia/tui/internal/daemon"
)

// API is everything the setup screens ask the backend. An interface so the
// screens' tests run against a scripted backend, not a daemon.
type API interface {
	Plan(ctx context.Context) (Plan, error)
	// ChooseChat saves the chat model. A model that may not fit answers with
	// an Ack until it is called again with ack set.
	ChooseChat(ctx context.Context, id string, ack bool) (Plan, *Ack, error)
	Skip(ctx context.Context, id string, skipped bool) (Plan, error)
	// Download pulls one model now. A disk shortfall answers with an Ack.
	Download(ctx context.Context, id string, ackLowDisk, keepTrying bool) (*Ack, error)
	Remove(ctx context.Context, id string) (Plan, error)
	// Run starts setup's own download of the plan. A disk shortfall answers
	// with an Ack.
	Run(ctx context.Context, req RunRequest) (*Ack, error)
	Status(ctx context.Context) (Status, error)
	// Cancel stops the run (id "") or one model's download.
	Cancel(ctx context.Context, id string) (bool, error)

	GPU(ctx context.Context, settingGB float64) (GPU, error)
	GPUApply(ctx context.Context, settingGB float64) (GPU, error)
	GPURevert(ctx context.Context) (GPU, error)
	GPUPending(ctx context.Context, settingGB float64) (GPU, error)
	GPUKeep(ctx context.Context) (GPU, error)
}

// StatusError is a refusal from the backend: its status code and the plain
// words it gave, which are written for the user.
type StatusError struct {
	Op     string
	Status int
	Detail string
}

func (e *StatusError) Error() string {
	if e.Detail == "" {
		return fmt.Sprintf("GAIA could not %s (HTTP %d)", e.Op, e.Status)
	}
	return e.Detail
}

// IsStatus reports whether err is a backend refusal with that status code.
func IsStatus(err error, status int) bool {
	var se *StatusError
	return errors.As(err, &se) && se.Status == status
}

// Daemon talks to the machine's GAIA daemon, starting it when it is not up.
type Daemon struct {
	c *daemon.Client
	// attachOnly never starts a daemon: a background probe must not leave a
	// process behind that nobody asked for.
	attachOnly bool
}

// NewDaemon builds the API over the GAIA daemon, starting it when needed.
func NewDaemon() *Daemon { return &Daemon{c: daemon.New(daemon.Options{})} }

// NewAttachedDaemon builds the API over a daemon that is already running; a
// call fails rather than start one.
func NewAttachedDaemon() *Daemon {
	return &Daemon{c: daemon.New(daemon.Options{}), attachOnly: true}
}

const (
	setupPath = daemon.APIPrefix + "/setup"
	gpuPath   = daemon.APIPrefix + "/gpu-memory"
	// maxBody bounds an answer. A plan lists every model Lemonade has, so it
	// is far larger than a status call, but never megabytes.
	maxBody = 4 << 20
)

// olderDaemon is what to do when the running daemon predates these routes.
const olderDaemon = "Restart it with `gaia daemon restart` so it runs the GAIA installed now"

func (d *Daemon) call(ctx context.Context, method, path, op string, body any, out any) (*Ack, error) {
	var inst *daemon.Instance
	var err error
	if d.attachOnly {
		inst, err = d.c.Attach(ctx)
	} else {
		inst, err = d.c.StartOrAttach(ctx)
	}
	if err != nil {
		return nil, fmt.Errorf("could not reach the GAIA background service to %s: %w", op, err)
	}
	var raw []byte
	header := http.Header{}
	if body != nil {
		if raw, err = json.Marshal(body); err != nil {
			return nil, err
		}
		header.Set("Content-Type", "application/json")
	}
	resp, _, err := d.c.Do(ctx, inst, daemon.Request{Method: method, Path: path, Body: raw, Header: header, Op: op})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return nil, fmt.Errorf("could not read the answer to %s: %w", op, err)
	}
	return decode(path, op, resp.StatusCode, data, out)
}

// decode turns one answer into the value, a confirmation to ask for, or an
// error that carries the backend's own words.
func decode(path, op string, status int, data []byte, out any) (*Ack, error) {
	if status >= 200 && status < 300 {
		if out == nil {
			return nil, nil
		}
		if err := json.Unmarshal(data, out); err != nil {
			return nil, fmt.Errorf("GAIA's answer to %s could not be read: %w", op, err)
		}
		return nil, nil
	}
	var body struct {
		Detail json.RawMessage `json:"detail"`
	}
	_ = json.Unmarshal(data, &body)
	if status == http.StatusConflict && len(body.Detail) > 0 && body.Detail[0] == '{' {
		var ack Ack
		if err := json.Unmarshal(body.Detail, &ack); err == nil && ack.Acknowledge != "" {
			return &ack, nil
		}
	}
	detail := ""
	if len(body.Detail) > 0 {
		if err := json.Unmarshal(body.Detail, &detail); err != nil {
			detail = string(body.Detail)
		}
	} else {
		detail = strings.TrimSpace(string(data))
	}
	if daemon.IsRouteMissing(path, status, detail) {
		return nil, &daemon.RouteMissingError{Op: op, Path: path, Alternative: olderDaemon}
	}
	return nil, &StatusError{Op: op, Status: status, Detail: detail}
}

func (d *Daemon) Plan(ctx context.Context) (Plan, error) {
	var p Plan
	_, err := d.call(ctx, http.MethodGet, setupPath+"/plan", "read setup's model plan", nil, &p)
	return p, err
}

func (d *Daemon) ChooseChat(ctx context.Context, id string, ack bool) (Plan, *Ack, error) {
	var p Plan
	a, err := d.call(ctx, http.MethodPost, setupPath+"/chat-model", "choose the chat model",
		map[string]any{"model_id": id, "acknowledge_no_fit": ack}, &p)
	return p, a, err
}

func (d *Daemon) Skip(ctx context.Context, id string, skipped bool) (Plan, error) {
	var p Plan
	_, err := d.call(ctx, http.MethodPost, setupPath+"/models/skip", "save which models to skip",
		map[string]any{"model_id": id, "skipped": skipped}, &p)
	return p, err
}

func (d *Daemon) Download(ctx context.Context, id string, ackLowDisk, keepTrying bool) (*Ack, error) {
	return d.call(ctx, http.MethodPost, setupPath+"/models/download", "download "+id,
		map[string]any{"model_id": id, "acknowledge_low_disk": ackLowDisk, "keep_trying": keepTrying}, nil)
}

func (d *Daemon) Remove(ctx context.Context, id string) (Plan, error) {
	var p Plan
	_, err := d.call(ctx, http.MethodPost, setupPath+"/models/remove", "remove "+id,
		map[string]any{"model_id": id}, &p)
	return p, err
}

func (d *Daemon) Run(ctx context.Context, req RunRequest) (*Ack, error) {
	return d.call(ctx, http.MethodPost, setupPath+"/run", "start downloading the models", req, nil)
}

func (d *Daemon) Status(ctx context.Context) (Status, error) {
	var s Status
	_, err := d.call(ctx, http.MethodGet, setupPath+"/status", "read the download progress", nil, &s)
	return s, err
}

func (d *Daemon) Cancel(ctx context.Context, id string) (bool, error) {
	var out struct {
		Cancelled bool `json:"cancelled"`
	}
	var body any
	if id != "" {
		body = map[string]any{"model_id": id}
	}
	_, err := d.call(ctx, http.MethodPost, setupPath+"/cancel", "stop the download", body, &out)
	return out.Cancelled, err
}

func (d *Daemon) GPU(ctx context.Context, settingGB float64) (GPU, error) {
	var g GPU
	path := gpuPath
	if settingGB > 0 {
		path += "?" + url.Values{"setting_gb": {strconv.FormatFloat(settingGB, 'f', -1, 64)}}.Encode()
	}
	_, err := d.call(ctx, http.MethodGet, path, "read how memory is split with the GPU", nil, &g)
	return g, err
}

func (d *Daemon) GPUApply(ctx context.Context, settingGB float64) (GPU, error) {
	var g GPU
	_, err := d.call(ctx, http.MethodPost, gpuPath+"/apply", "change the GPU memory",
		map[string]any{"setting_gb": settingGB, "confirm": true}, &g)
	return g, err
}

func (d *Daemon) GPURevert(ctx context.Context) (GPU, error) {
	var g GPU
	_, err := d.call(ctx, http.MethodPost, gpuPath+"/revert", "undo the GPU memory change",
		map[string]any{"confirm": true}, &g)
	return g, err
}

func (d *Daemon) GPUPending(ctx context.Context, settingGB float64) (GPU, error) {
	var g GPU
	_, err := d.call(ctx, http.MethodPost, gpuPath+"/pending", "remember the GPU memory change",
		map[string]any{"setting_gb": settingGB}, &g)
	return g, err
}

func (d *Daemon) GPUKeep(ctx context.Context) (GPU, error) {
	var g GPU
	_, err := d.call(ctx, http.MethodDelete, gpuPath+"/pending", "keep the current GPU memory", nil, &g)
	return g, err
}
