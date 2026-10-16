// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import (
	"errors"
	"reflect"
	"sync"
	"testing"
)

type recorder struct {
	mu  sync.Mutex
	log []string
	err error
}

func (r *recorder) add(s string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.log = append(r.log, s)
}

func (r *recorder) acquire(_ func(error)) (func() error, error) {
	if r.err != nil {
		r.add("refused")
		return nil, r.err
	}
	r.add("hold")
	return func() error { r.add("release"); return nil }, nil
}

func TestKeeperAppliesHoldsInOrder(t *testing.T) {
	r := &recorder{}
	k := newKeeper(r.acquire)
	for _, on := range []bool{true, true, false, false, true, false, true} {
		k.Hold(on)
	}
	k.Close() // releases the last hold
	want := []string{"hold", "release", "hold", "release", "hold", "release"}
	if !reflect.DeepEqual(r.log, want) {
		t.Fatalf("got %v, want %v", r.log, want)
	}
}

func TestKeeperReportsTheFirstFailureOnce(t *testing.T) {
	r := &recorder{err: errors.New("no power manager")}
	k := newKeeper(r.acquire)
	k.Hold(true)
	k.Hold(false)
	k.Hold(true)
	k.Close()
	if err := <-k.Errors(); err == nil || err.Error() != "no power manager" {
		t.Fatalf("got %v", err)
	}
	select {
	case err := <-k.Errors():
		t.Fatalf("a second report: %v", err)
	default:
	}
	if !reflect.DeepEqual(r.log, []string{"refused", "refused"}) {
		t.Fatalf("got %v", r.log)
	}
}

// The real platform call, end to end: it must hold and release cleanly on
// the machine running the tests.
func TestPlatformKeepAwake(t *testing.T) {
	release, err := acquireAwake(func(e error) { t.Errorf("hold lost: %v", e) })
	if err != nil {
		t.Skipf("this machine cannot keep awake: %v", err)
	}
	if err := release(); err != nil {
		t.Fatalf("release: %v", err)
	}
}
