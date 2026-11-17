// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package hybridapitest is a scripted hybridapi.API for screen tests.
package hybridapitest

import (
	"context"
	"sync"

	"github.com/amd/gaia/tui/internal/event"
	"github.com/amd/gaia/tui/internal/hybridapi"
)

// Fake answers with its fields and records what it was sent.
type Fake struct {
	mu sync.Mutex

	View         hybridapi.Settings
	SettingsErr  error
	UpdateFn     func(changes map[string]any) (hybridapi.Settings, error)
	TemplateList []hybridapi.Template
	TemplatesErr error
	ApplyFn      func(id string) (hybridapi.Settings, error)
	Route        event.CanonicalRoute
	PreviewErr   error
	StatsAnswer  hybridapi.Stats
	StatsErr     error
	EntryAnswer  hybridapi.Entry
	EntryErr     error

	Updates  []map[string]any
	Applied  []string
	Previews []string
}

func (f *Fake) Settings(context.Context) (hybridapi.Settings, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.View, f.SettingsErr
}

func (f *Fake) Update(_ context.Context, changes map[string]any) (hybridapi.Settings, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Updates = append(f.Updates, changes)
	if f.UpdateFn != nil {
		return f.UpdateFn(changes)
	}
	return f.View, nil
}

func (f *Fake) Templates(context.Context) ([]hybridapi.Template, error) {
	return f.TemplateList, f.TemplatesErr
}

func (f *Fake) ApplyTemplate(_ context.Context, id string) (hybridapi.Settings, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Applied = append(f.Applied, id)
	if f.ApplyFn != nil {
		return f.ApplyFn(id)
	}
	return f.View, nil
}

func (f *Fake) Preview(_ context.Context, text string) (event.CanonicalRoute, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Previews = append(f.Previews, text)
	return f.Route, f.PreviewErr
}

func (f *Fake) Stats(context.Context) (hybridapi.Stats, error) { return f.StatsAnswer, f.StatsErr }

func (f *Fake) Entry(context.Context) (hybridapi.Entry, error) { return f.EntryAnswer, f.EntryErr }
