// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package attention

import "runtime"

// Keeper holds the OS's keep-awake request on one goroutine of its own.
//
// One goroutine, because on Windows the request belongs to the thread that
// made it and must be released from that same thread; and because holds and
// releases then apply in the order the turns started and ended.
type Keeper struct {
	reqs chan bool
	errs chan error
	done chan struct{}
	// acquire takes the hold and returns its release; lost is called if the
	// hold ends on its own. Swappable for tests.
	acquire func(lost func(error)) (release func() error, err error)
}

// NewKeeper starts a keeper using this platform's keep-awake call.
func NewKeeper() *Keeper { return newKeeper(acquireAwake) }

func newKeeper(acquire func(lost func(error)) (func() error, error)) *Keeper {
	k := &Keeper{
		reqs:    make(chan bool, 16),
		errs:    make(chan error, 1),
		done:    make(chan struct{}),
		acquire: acquire,
	}
	go k.run()
	return k
}

// Hold asks for the PC to be kept awake (true) or allowed to sleep (false).
func (k *Keeper) Hold(on bool) { k.reqs <- on }

// Errors delivers the first failure, including a hold that ended on its own
// mid-turn. Later ones are dropped: the user is told once per session, not
// once per turn.
func (k *Keeper) Errors() <-chan error { return k.errs }

// Close releases any hold and stops the keeper.
func (k *Keeper) Close() {
	close(k.reqs)
	<-k.done
}

func (k *Keeper) run() {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	defer close(k.done)

	var release func() error
	for on := range k.reqs {
		switch {
		case on && release == nil:
			r, err := k.acquire(k.report)
			if err != nil {
				k.report(err)
				continue
			}
			release = r
		case !on && release != nil:
			if err := release(); err != nil {
				k.report(err)
			}
			release = nil
		}
	}
	if release != nil {
		if err := release(); err != nil {
			k.report(err)
		}
	}
}

func (k *Keeper) report(err error) {
	select {
	case k.errs <- err:
	default:
	}
}
