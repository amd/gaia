// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package setupapitest is an in-memory setup backend for tests: it keeps a
// plan and a status, applies choices to them the way the daemon does, and
// records every call so a test can assert what was sent.
package setupapitest

import (
	"context"
	"fmt"
	"strings"
	"sync"

	"github.com/amd/gaia/tui/internal/setupapi"
)

// Fake is a scripted setup backend.
type Fake struct {
	mu sync.Mutex

	P         setupapi.Plan
	PlanErr   error
	S         setupapi.Status
	StatusErr error
	G         *setupapi.GPU
	GPUErr    error
	// ApplyErr answers GPUApply, e.g. a 403 for a refused elevation.
	ApplyErr error
	// NoFit lists models whose chat choice needs confirming.
	NoFit map[string]setupapi.Ack
	// LowDisk makes Run and Download ask to confirm the disk once.
	LowDisk *setupapi.Ack
	// CancelLater leaves rows as they are on Cancel, the way a pull that stops
	// at its next chunk still reports "downloading" for a moment.
	CancelLater bool
	// RunErr and DownloadErr refuse to start.
	RunErr      error
	DownloadErr error

	Calls []string
}

func (f *Fake) record(format string, args ...any) {
	f.Calls = append(f.Calls, fmt.Sprintf(format, args...))
}

// Called reports whether a call starting with prefix was made.
func (f *Fake) Called(prefix string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.Calls {
		if strings.HasPrefix(c, prefix) {
			return true
		}
	}
	return false
}

// CallList is a copy of the calls so far.
func (f *Fake) CallList() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.Calls...)
}

// SetStatus replaces what Status answers.
func (f *Fake) SetStatus(s setupapi.Status) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.S = s
}

// SetRunError sets why the run stopped and whether a download failed in it.
func (f *Fake) SetRunError(modelFailed bool, msg string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.S.ModelFailed = modelFailed
	f.S.Error = &msg
}

// SetStatusErr makes Status fail (a dropped daemon), or recover with nil.
func (f *Fake) SetStatusErr(err error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.StatusErr = err
}

func (f *Fake) Plan(context.Context) (setupapi.Plan, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("plan")
	return f.P, f.PlanErr
}

func (f *Fake) ChooseChat(_ context.Context, id string, ack bool) (setupapi.Plan, *setupapi.Ack, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("chat %s ack=%v", id, ack)
	if a, ok := f.NoFit[id]; ok && !ack {
		return f.P, &a, nil
	}
	f.P.ChatModel.ID = id
	for i := range f.P.Catalog {
		e := &f.P.Catalog[i]
		if same(e.ID, id) {
			e.Planned, e.Skipped, e.Selected = true, false, true
		}
	}
	return f.P, nil, nil
}

func (f *Fake) Skip(_ context.Context, id string, skipped bool) (setupapi.Plan, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("skip %s %v", id, skipped)
	for i := range f.P.Catalog {
		e := &f.P.Catalog[i]
		if same(e.ID, id) {
			e.Skipped = skipped
			e.Selected = e.Planned && !skipped
		}
	}
	return f.P, nil
}

func (f *Fake) Download(_ context.Context, id string, ack, keep bool) (*setupapi.Ack, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("download %s ack=%v keep=%v", id, ack, keep)
	if f.LowDisk != nil && !ack {
		a := *f.LowDisk
		return &a, nil
	}
	if f.DownloadErr != nil {
		return nil, f.DownloadErr
	}
	f.upsert(setupapi.ModelProgress{ID: id, State: setupapi.StateDownloading})
	return nil, nil
}

func (f *Fake) Remove(_ context.Context, id string) (setupapi.Plan, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("remove %s", id)
	for i := range f.P.Catalog {
		e := &f.P.Catalog[i]
		if same(e.ID, id) {
			no := false
			e.Downloaded, e.Skipped, e.Selected = &no, true, false
		}
	}
	return f.P, nil
}

func (f *Fake) Run(_ context.Context, req setupapi.RunRequest) (*setupapi.Ack, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("run cloud=%v ack=%v keep=%v", req.SkipChatModel, req.AcknowledgeLowDisk, req.KeepTrying)
	if f.LowDisk != nil && !req.AcknowledgeLowDisk {
		a := *f.LowDisk
		return &a, nil
	}
	if f.RunErr != nil {
		return nil, f.RunErr
	}
	f.S.State = setupapi.RunRunning
	return nil, nil
}

func (f *Fake) Status(context.Context) (setupapi.Status, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("status")
	if f.StatusErr != nil {
		return setupapi.Status{}, f.StatusErr
	}
	s := f.S
	s.Models = append([]setupapi.ModelProgress(nil), f.S.Models...)
	return s, nil
}

func (f *Fake) Cancel(_ context.Context, id string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("cancel %s", id)
	if f.CancelLater {
		return true, nil
	}
	if f.S.State == setupapi.RunRunning {
		f.S.State = setupapi.RunCancelled
	}
	for i := range f.S.Models {
		m := &f.S.Models[i]
		if (id == "" || same(m.ID, id)) && (m.State == setupapi.StateDownloading || m.State == setupapi.StateQueued) {
			m.State = setupapi.StateCancelled
		}
	}
	return true, nil
}

func (f *Fake) GPU(_ context.Context, setting float64) (setupapi.GPU, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("gpu %v", setting)
	if f.GPUErr != nil || f.G == nil {
		err := f.GPUErr
		if err == nil {
			err = fmt.Errorf("no GPU reading")
		}
		return setupapi.GPU{}, err
	}
	return *f.G, nil
}

func (f *Fake) GPUApply(_ context.Context, setting float64) (setupapi.GPU, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("gpu-apply %v", setting)
	if f.ApplyErr != nil {
		return setupapi.GPU{}, f.ApplyErr
	}
	g := *f.G
	prev := g.Setting.CurrentGB
	g.Pending = &setupapi.GPUPending{SettingGB: setting, PreviousSettingGB: &prev, Source: "apply", State: setupapi.PendingRestart}
	f.G = &g
	return g, nil
}

func (f *Fake) GPURevert(context.Context) (setupapi.GPU, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("gpu-revert")
	return *f.G, nil
}

func (f *Fake) GPUPending(_ context.Context, setting float64) (setupapi.GPU, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("gpu-pending %v", setting)
	g := *f.G
	g.Pending = &setupapi.GPUPending{SettingGB: setting, Source: "guided", State: setupapi.PendingRestart}
	f.G = &g
	return g, nil
}

func (f *Fake) GPUKeep(context.Context) (setupapi.GPU, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("gpu-keep")
	g := *f.G
	g.Pending = nil
	f.G = &g
	return g, nil
}

// upsert sets one model's progress row. Caller holds the lock.
func (f *Fake) upsert(p setupapi.ModelProgress) {
	for i := range f.S.Models {
		if same(f.S.Models[i].ID, p.ID) {
			total := f.S.Models[i].BytesTotal
			f.S.Models[i] = p
			if p.BytesTotal == 0 {
				f.S.Models[i].BytesTotal = total
			}
			return
		}
	}
	f.S.Models = append(f.S.Models, p)
}

// Progress sets one model's row, adding it when new.
func (f *Fake) Progress(p setupapi.ModelProgress) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.upsert(p)
}

// SetRunState sets the run's state.
func (f *Fake) SetRunState(state string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.S.State = state
}

func same(a, b string) bool {
	return strings.EqualFold(strings.TrimPrefix(a, "user."), strings.TrimPrefix(b, "user."))
}

// Entry builds a catalog entry; sizes in decimal GB.
func Entry(id string, roles []string, size float64, downloaded, planned, recommended bool, fits *bool, need, have float64) setupapi.CatalogEntry {
	full := size
	left := size
	if downloaded {
		left = 0
	}
	d := downloaded
	e := setupapi.CatalogEntry{
		ID: id, Name: strings.TrimSuffix(strings.TrimPrefix(id, "user."), "-GGUF"), Roles: roles, SizeGB: &left, FullSizeGB: &full,
		Downloaded: &d, InLemonade: true, Recommended: recommended,
		Planned: planned, Selected: planned, ChatChoice: !hasRole(roles, setupapi.RoleEmbedding),
		Fits: fits,
	}
	if fits != nil {
		e.NeedGB, e.CapacityGB = &need, &have
	}
	return e
}

func hasRole(roles []string, r string) bool {
	for _, x := range roles {
		if x == r {
			return true
		}
	}
	return false
}

// Bool is a pointer to b.
func Bool(b bool) *bool { return &b }

// Float is a pointer to f.
func Float(f float64) *float64 { return &f }
