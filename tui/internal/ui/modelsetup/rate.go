// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"math"
	"time"
)

// Rates and ETAs are smoothed on this side: the backend reports a 2 s window,
// which jumps. The rules come from the setup spec: speed is a 5 s moving
// average; the ETA appears only after 10 s of data and while bytes move; and a
// download with no new bytes for 15 s says so, then after 60 s offers a retry.
const (
	speedWindow = 5 * time.Second
	etaAfter    = 10 * time.Second
	stallNotice = 15 * time.Second
	stallStuck  = 60 * time.Second
)

// rate follows one download (or the overall total) across status reads.
type rate struct {
	started    time.Time // first read that saw bytes move; speed counts from here
	startBytes int64
	lastAt     time.Time
	lastBytes  int64
	movedAt    time.Time // last read whose bytes grew
	speed      float64   // smoothed bytes per second
	eta        time.Duration
	etaAt      time.Time
	hasETA     bool
}

// observe folds in one status read: bytes done of total at now.
func (r *rate) observe(now time.Time, done, total int64) {
	if r.lastAt.IsZero() {
		r.lastAt, r.lastBytes, r.movedAt = now, done, now
		return
	}
	dt := now.Sub(r.lastAt)
	if dt <= 0 {
		return
	}
	delta := done - r.lastBytes
	if delta < 0 {
		// A restarted pull: start the average over.
		*r = rate{lastAt: now, lastBytes: done, movedAt: now}
		return
	}
	if r.started.IsZero() {
		if delta > 0 {
			// The first growth is the baseline, not a speed: a resumed pull
			// reports the bytes already on disk in one jump.
			r.started, r.startBytes, r.movedAt = now, done, now
		}
		r.lastAt, r.lastBytes = now, done
		return
	}
	if delta > 0 {
		r.movedAt = now
	}
	if since := now.Sub(r.started); since <= speedWindow {
		// Too little data for a moving average: the mean since the start,
		// so one slow first second doesn't linger.
		r.speed = float64(done-r.startBytes) / since.Seconds()
	} else {
		inst := float64(delta) / dt.Seconds()
		alpha := 1 - math.Exp(-dt.Seconds()/speedWindow.Seconds())
		r.speed += alpha * (inst - r.speed)
	}
	r.lastAt, r.lastBytes = now, done
	r.updateETA(now, total-done)
}

// updateETA moves the shown ETA toward the measured one, smoothed so it
// doesn't jump with every read but free to move either way. Until it is shown
// it simply tracks the measurement.
func (r *rate) updateETA(now time.Time, left int64) {
	if r.speed <= 0 || left <= 0 {
		r.hasETA = false
		return
	}
	target := time.Duration(float64(left) / r.speed * float64(time.Second))
	if !r.hasETA || !r.showETA(now) {
		r.eta, r.etaAt, r.hasETA = target, now, true
		return
	}
	elapsed := now.Sub(r.etaAt)
	counted := r.eta - elapsed
	k := math.Min(1, elapsed.Seconds()/speedWindow.Seconds())
	next := counted + time.Duration(float64(target-counted)*k)
	if next < 0 {
		next = 0
	}
	r.eta, r.etaAt = next, now
}

// showETA reports whether the ETA has earned its place on screen.
func (r *rate) showETA(now time.Time) bool {
	return r.hasETA && !r.started.IsZero() && now.Sub(r.started) >= etaAfter && r.speed > 0
}

// stalled is how long no bytes have arrived, zero while they move.
func (r *rate) stalled(now time.Time) time.Duration {
	if r.movedAt.IsZero() {
		return 0
	}
	return now.Sub(r.movedAt)
}
