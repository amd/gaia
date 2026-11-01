// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/setupapi"
	f "github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

// --- choose your models ------------------------------------------------------

// Fresh machine: the choice comes first and nothing downloads until Continue.
func TestColdMachineShowsThePlanBeforeAnyDownload(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)

	if h.m.Screen() != "models" {
		t.Fatalf("screen = %s, want models", h.m.Screen())
	}
	for _, c := range api.CallList() {
		if strings.HasPrefix(c, "run") || strings.HasPrefix(c, "download") {
			t.Fatalf("%q was sent before the user pressed Continue", c)
		}
	}
	h.expect("Choose your models")
	h.expect("[x] Qwen3.6-35B-A3B")
	h.expect("[x] Gemma-4-E4B-it")
	h.expect("[x] embeddinggemma-300m")
	h.expect("[ ] gpt-oss-20b")
	h.expect("* recommended")
	// Remaining download, decimal GB, one decimal; below 1 GB in MB.
	h.expect("23.3 GB")
	h.expect("330 MB")
	h.expect("Download 29.6 GB · free on C: 412.0 GB · after 382.4 GB")
	h.expect("step 2 of 3")
}

// Already-downloaded rows say so; a partial one shows what is left.
func TestSizesAreWhatIsLeftToDownload(t *testing.T) {
	p := coldPlan()
	p.Catalog[1] = f.Entry(gemma, vision, 5.97, true, true, true, f.Bool(true), 8.4, 96)
	left := 1.2
	p.Catalog[0].SizeGB, p.Catalog[0].Partial, p.Catalog[0].PartialGB = &left, true, 22.1
	h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})

	h.expect("on PC")
	h.expect("1.2 GB left")
	h.expect("Download 1.5 GB")
	// A partial download found at load offers to resume, never auto-resumes.
	h.expect(resumeBanner)
	if h.api.Called("run") {
		t.Fatal("a partial download must not resume on its own")
	}
}

func TestEverythingPresentGoesStraightToReady(t *testing.T) {
	h := newHarness(t, ModeFirstRun, &f.Fake{P: warmPlan(), G: discreteGPU()})
	if h.m.Screen() != "ready" {
		t.Fatalf("screen = %s, want ready", h.m.Screen())
	}
	h.expect(everythingHere)
	h.expect("m change models")
	h.key("m")
	if h.m.Screen() != "models" {
		t.Fatal("m must reopen the model choice")
	}
}

// Unticking Gemma warns, in words, and does not block.
func TestUntickingGemmaShowsTheVisionCaveatAndDoesNotBlock(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.focusID(gemma)
	h.key("space")

	h.expect("[ ] Gemma-4-E4B-it")
	h.expect("Without Gemma-4-E4B-it, GAIA can't read images or screenshots.")
	h.expect("enter continue")
	h.key("enter")
	if !api.Called("skip " + gemma + " true") {
		t.Fatalf("Continue did not save the skip: %v", api.CallList())
	}
	if !api.Called("run") {
		t.Fatalf("Continue did not start the download: %v", api.CallList())
	}
}

// A model that won't fit warns with real numbers and can be confirmed past.
func TestTooBigModelAsksWithRealNumbersAndCanBeConfirmed(t *testing.T) {
	api := &f.Fake{P: small16(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.focusID(qwen)
	h.key("space")
	h.expect("[!] needs ~27 GB; this PC has 16 GB.")
	h.expect("[!] won't fit")

	h.key("enter")
	h.expect("Needs ~27 GB, this PC has 16 GB.")
	h.expect("y download anyway")
	h.expect("n pick another")
	if api.Called("run") {
		t.Fatal("downloaded before the user confirmed")
	}

	// n: pick another — the cursor lands on the model in question.
	h.key("n")
	if r, _ := h.m.focusedRow(); r.id != qwen {
		t.Errorf("focus = %s, want %s", r.id, qwen)
	}
	h.key("enter", "y")
	// Setup's own models download first; Qwen3.6, not one of them, follows.
	if !api.Called("run") {
		t.Fatalf("confirmed download did not start: %v", api.CallList())
	}
	if len(h.m.dl.queue) != 1 || h.m.dl.queue[0] != qwen {
		t.Fatalf("queue = %v, want Qwen3.6 after setup's run", h.m.dl.queue)
	}
	h.expect("Qwen3.6-35B-A3B queued")
	api.SetRunState(setupapi.RunReady)
	h.poll()
	if !api.Called("download " + qwen) {
		t.Fatalf("Qwen3.6 did not download after the run: %v", api.CallList())
	}
}

func TestDiskShortfallNamesHowMuchToFreeAndTheModelToDrop(t *testing.T) {
	p := coldPlan()
	p.Disk.FreeGB = f.Float(10)
	api := &f.Fake{P: p, G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)

	h.expect("Needs 29.6 GB; only 10.0 GB free on C:. Free 19.6 GB or untick Qwen3.6-35B-A3B (saves 23.3 GB).")
	h.expect("enter continue anyway")

	// Every tick updates the totals.
	h.focusID(qwen)
	h.key("space")
	h.expect("Download 6.3 GB · free on C: 10.0 GB · after 3.7 GB")
	h.expectNot("Free 19.6 GB")
	h.key("space")

	h.key("enter")
	h.expect("y continue anyway")
	h.key("y")
	if !api.Called("run cloud=false ack=true") {
		t.Fatalf("the confirmed run must acknowledge the low disk: %v", api.CallList())
	}
}

func TestShortfallWithNoSingleModelToDropSaysUntickMore(t *testing.T) {
	p := coldPlan()
	p.Disk.FreeGB = f.Float(1)
	h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})
	h.expect("Free 28.6 GB, or untick more models.")
}

func TestUnknownFreeSpaceNeverGuesses(t *testing.T) {
	p := coldPlan()
	p.Disk.FreeGB = nil
	h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})
	h.expect("free space unknown")
	h.expectNot("after")
	h.expect("enter continue")
}

// fits null: "fit unknown", never a made-up number, and nothing to confirm.
func TestUnknownFitNeverInventsANumber(t *testing.T) {
	p := coldPlan()
	for i := range p.Catalog {
		p.Catalog[i].Fits, p.Catalog[i].NeedGB, p.Catalog[i].CapacityGB = nil, nil, nil
	}
	p.CapacityGB = nil
	api := &f.Fake{P: p, G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.expect("[?] fit unknown")
	h.focusID(qwen)
	h.expect(fitUnknownLine)
	if s := h.screen(); strings.Contains(s, "Needs about") || strings.Contains(s, "needs ~") {
		t.Fatalf("a number was invented for an unknown fit:\n%s", s)
	}
	h.key("enter")
	if !api.Called("run") {
		t.Fatal("an unknown fit must not ask for confirmation")
	}
}

// An FLM-only user picks the FLM model and finishes with no downloads.
func TestFLMOnlyUserFinishesWithNoDownloads(t *testing.T) {
	api := &f.Fake{P: flmOnly(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.expect("1 more already in Lemonade (press l)")
	h.key("l")
	h.expect("gemma4-it-e2b-FLM")
	h.expect("NPU")
	h.focusID(flm)
	h.key("space")
	h.expect("Default chat: gemma4-it-e2b-FLM (NPU)")
	h.focusID(gemma)
	h.key("space")
	h.focusID(emb)
	h.key("space")
	h.expect("Download 0 MB")
	h.key("enter")

	if !api.Called("chat " + flm) {
		t.Fatalf("the FLM model was not saved as the chat model: %v", api.CallList())
	}
	if api.Called("run") || api.Called("download") {
		t.Fatalf("an FLM-only finish must download nothing: %v", api.CallList())
	}
	if h.m.Screen() != "ready" {
		t.Fatalf("screen = %s, want ready", h.m.Screen())
	}
	h.expect("Chat       gemma4-it-e2b-FLM   (on this PC, NPU)")
	h.expect("Vision: off.")
	h.expect("Documents and memory: off.")
	// Two models were left out, so it must not claim everything is here.
	h.expectNot(everythingHere)
}

// Zero chat models with local chat is the one disabled Continue, with its
// reason in words.
func TestNoChatModelIsTheOnlyDisabledContinue(t *testing.T) {
	api := &f.Fake{P: small16(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.focusID(gemma)
	h.key("space")
	h.expect(zeroChat)
	h.expectNot("enter continue")
	h.expect("Default chat: none")
	h.key("enter")
	if api.Called("run") || api.Called("skip") {
		t.Fatal("Continue went ahead with no chat model")
	}
	// Ticking any chat model clears it.
	h.focusID(qwen)
	h.key("space")
	h.expectNot(zeroChat)
}

func TestCloudChatOffersOnlyTheEmbedder(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api, func(o *Options) { o.Cloud = true })
	h.expect(cloudNoDownload)
	h.expect("embeddinggemma-300m")
	h.expectNot("Qwen3.6")
	h.key("enter")
	if !api.Called("run cloud=true") {
		t.Fatalf("a cloud run must skip the chat model: %v", api.CallList())
	}
}

func TestResetTicksTheRecommendedSetOnly(t *testing.T) {
	h := newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: discreteGPU()})
	h.focusID(oss)
	h.key("space")
	h.focusID(gemma)
	h.key("space")
	h.key("a")
	h.expect("[x] Gemma-4-E4B-it")
	h.expect("[ ] gpt-oss-20b")
}

func TestChatPickerChoosesTheDefault(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.focusID(oss)
	h.key("space", "c")
	if h.m.Screen() != "chat-pick" {
		t.Fatalf("c did not open the picker: %s", h.m.Screen())
	}
	h.key("down", "down", "enter")
	h.expect("Default chat: gpt-oss-20b")
	h.key("enter")
	if !api.Called("chat " + oss) {
		t.Fatalf("the picked chat model was not saved: %v", api.CallList())
	}
}

func TestBackPreservesTheSelection(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: gpuShared()}
	h := newHarness(t, ModeFirstRun, api)
	h.focusID(oss)
	h.key("space", "enter")
	if h.m.Screen() != "gpu" {
		t.Fatalf("screen = %s, want gpu", h.m.Screen())
	}
	h.key("esc")
	if h.m.Screen() != "models" {
		t.Fatalf("esc on the GPU step went to %s", h.m.Screen())
	}
	h.expect("[x] gpt-oss-20b")
	h.key("esc")
	// esc on the first screen leaves to the host.
	var got bool
	h.m, _ = h.m.Update(keyMsg("esc"))
	_, cmd := h.m.Update(keyMsg("esc"))
	for _, msg := range collect(cmd) {
		if _, ok := msg.(BackMsg); ok {
			got = true
		}
	}
	if !got {
		t.Fatal("esc on step 2 must hand back to the host")
	}
}

// --- GPU memory step ---------------------------------------------------------

func TestDiscreteGPUHasNoGPUStepAndContiguousNumbers(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.expect("step 2 of 3")
	h.key("enter")
	if h.m.Screen() != "download" {
		t.Fatalf("screen = %s, want download", h.m.Screen())
	}
	h.expect("step 3 of 3")
}

func TestUnknownGPUHasNoGPUStep(t *testing.T) {
	api := &f.Fake{P: coldPlan(), GPUErr: errDropped}
	h := newHarness(t, ModeFirstRun, api)
	h.expect("step 2 of 3")
}

func TestIntegratedGPUShowsTheStepWithConsentAndCancelByDefault(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: gpuShared()}
	h := newHarness(t, ModeFirstRun, api)
	h.expect("step 2 of 4")
	h.key("enter")
	h.expect("Give the GPU more memory")
	h.expect("step 3 of 4")
	h.expect("This PC shares 128 GB between the CPU and the GPU. The GPU has 24 GB now.")
	h.expect("CPU 104 GB  |  GPU 24 GB")
	// The GPU memory the user reads, and the setting that gives it.
	h.expect("Target: GPU 40 GB")
	h.expect("Set Variable Graphics Memory to 32 GB for this.")
	h.expect("GAIA keeps at least 16 GB for Windows and your apps.")
	h.expect("With 40 GB for the GPU, Qwen3.6-35B-A3B fits.")

	h.key("right")
	h.expect("Target: GPU 72 GB")
	h.expect("With 72 GB for the GPU, Qwen3.6-35B-A3B and gpt-oss-120b-mxfp fit.")
	h.key("left")

	h.key("enter")
	h.expect(gpuConsentKey)
	h.expect("change the GPU memory to 40 GB (Variable Graphics Memory 32 GB)")
	h.expect("Current setting: 16 GB, saved for undo.")
	h.expect("y continue")
	h.expect("n cancel")
	// esc is no: the safe answer.
	h.key("esc")
	if api.Called("gpu-apply") {
		t.Fatal("cancel applied the change")
	}
	h.key("enter", "y")
	// The setting goes to the backend, never the GPU total.
	if !api.Called("gpu-apply 32") {
		t.Fatalf("y did not apply: %v", api.CallList())
	}
	h.expect("Done. Restart your PC to use 40 GB.")
	h.key("enter")
	if h.m.Screen() != "download" {
		t.Fatalf("after the GPU step the download should start, got %s", h.m.Screen())
	}
}

func TestRefusedElevationChangesNothingAndOffersThreeWays(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: gpuShared(),
		ApplyErr: &setupapi.StatusError{Op: "change the GPU memory", Status: 403, Detail: "admin permission not given"}}
	h := newHarness(t, ModeFirstRun, api)
	h.key("enter", "enter", "y")
	h.expect(gpuDeniedText)
	h.expect("t try again")
	h.expect("h show me how")
	h.expect("s skip")
}

func TestStepHiddenWhenTheUserGaveUpEveryModelThatGains(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: gpuShared()}
	h := newHarness(t, ModeFirstRun, api)
	h.focusID(qwen)
	h.key("space") // Qwen unticked; the default moves to Gemma? no: to oss? none ticked
	h.focusID(oss)
	h.key("space")
	h.expect("step 2 of 3")
}

// "I've done this" re-reads the split; it never trusts the claim.
func TestGuidedWalkthroughRereadsTheSplit(t *testing.T) {
	g := gpuShared()
	g.Method = setupapi.MethodGuided
	api := &f.Fake{P: coldPlan(), G: g}
	h := newHarness(t, ModeFirstRun, api)
	h.key("enter", "enter")
	if h.m.Screen() != "gpu-guide" {
		t.Fatalf("enter on a guided PC should open the steps, got %s", h.m.Screen())
	}
	h.expect("1. Open AMD Software: Adrenalin Edition.")
	h.expect("Value: 30")
	h.key("enter")
	if !api.Called("gpu 32") {
		t.Fatalf("I've done this did not re-read the split: %v", api.CallList())
	}
	// Still the old setting: a restart is pending, recorded for the next start.
	if !api.Called("gpu-pending 32") {
		t.Fatalf("an unchanged split must be recorded as pending: %v", api.CallList())
	}
	h.expect("Restart your PC to use 40 GB.")
}

func TestAfterRestartTheSplitIsReportedHonestly(t *testing.T) {
	g := gpuShared()
	g.Pending = &setupapi.GPUPending{SettingGB: 32, State: setupapi.PendingNotApplied}
	h := newHarness(t, ModeGPU, &f.Fake{G: g})
	h.expect("The PC still reports 24 GB. The change may not have applied.")
	h.expect("k keep 24 GB")
	h.key("k")
	if !h.api.Called("gpu-keep") {
		t.Fatal("keep did not clear the pending record")
	}

	g2 := gpuShared()
	g2.GPUGB, g2.Setting.CurrentGB = 40, 32
	g2.Pending = &setupapi.GPUPending{SettingGB: 32, State: setupapi.PendingTookEffect}
	h2 := newHarness(t, ModeGPU, &f.Fake{G: g2})
	h2.expect("GPU memory is now 40 GB.")
}

// --- download ---------------------------------------------------------------

func startedDownload(t *testing.T) *harness {
	t.Helper()
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.key("enter")
	for _, id := range []string{emb, qwen, gemma} {
		api.Progress(setupapi.ModelProgress{ID: id, State: setupapi.StateQueued, BytesTotal: total(id)})
	}
	h.poll()
	return h
}

func total(id string) int64 {
	switch id {
	case qwen:
		return 23_300_000_000
	case gemma:
		return 5_970_000_000
	}
	return 330_000_000
}

func TestProgressShowsEveryStateInWords(t *testing.T) {
	h := startedDownload(t)
	h.expect("Downloading models")
	h.expect("queued")
	h.expect(leavingCancels)

	h.api.Progress(setupapi.ModelProgress{ID: emb, State: setupapi.StateReady, BytesDone: total(emb), BytesTotal: total(emb)})
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 1e9, BytesTotal: total(qwen), SpeedBPS: 40e6})
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateVerifying, BytesDone: total(gemma), BytesTotal: total(gemma)})
	h.poll()
	h.expect("[ok]")
	h.expect("ready")
	h.expect("[..]")
	h.expect("1.0/23.3 GB")
	// Downloaded while another still downloads: waiting for the load check.
	h.expect(verifyWaiting)
	h.expect(estimating)

	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateFailed, BytesDone: 2e9, BytesTotal: total(qwen),
		ErrorKind: strp("network"), CanRetry: true})
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateCancelled, BytesDone: 3e9, BytesTotal: total(gemma)})
	h.poll()
	h.expect("[!]")
	h.expect("failed")
	// What was on disk stays counted: it verified at 6.0 GB before it stopped.
	h.expect("cancelled, 6.0 GB kept")
	h.expect("1 failed")
}

func TestSpeedAndETAAppearAfterTenSecondsAndNeverJump(t *testing.T) {
	h := startedDownload(t)
	var done int64
	var etas []time.Duration
	speeds := []int64{40e6, 40e6, 200e6, 10e6, 40e6, 40e6, 300e6, 40e6, 40e6, 40e6, 40e6, 40e6, 40e6, 40e6}
	for i, s := range speeds {
		done += s
		h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: done, BytesTotal: total(qwen), SpeedBPS: s})
		h.tick(time.Second)
		r := h.m.rates[qwen]
		if r == nil {
			t.Fatal("no rate kept")
		}
		if h.clock.Sub(r.started) < etaAfter && r.showETA(h.clock) {
			t.Fatalf("ETA shown after only %v", h.clock.Sub(r.started))
		}
		if r.hasETA {
			etas = append(etas, r.eta)
		}
		_ = i
	}
	h.expect("MB/s")
	h.expect("min")
	for i := 1; i < len(etas); i++ {
		// One second passes per read: the shown ETA may drop by at most that.
		if etas[i] < etas[i-1]-time.Second-time.Millisecond {
			t.Fatalf("ETA jumped down faster than real time: %v -> %v", etas[i-1], etas[i])
		}
	}
}

func TestStalledDownloadSaysSoThenOffersRetry(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 1e9, BytesTotal: total(qwen)})
	h.poll()
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 2e9, BytesTotal: total(qwen)})
	h.poll()
	h.tick(16 * time.Second)
	h.expect(stalledShort)
	h.tick(50 * time.Second)
	h.expect("Download seems stuck.")
}

func TestCancelKeepsFilesAndResumeContinues(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 7_600_000_000, BytesTotal: total(qwen)})
	h.poll()
	h.key("c")
	h.expect(cancelAsk)
	h.key("n")
	if h.api.Called("cancel") {
		t.Fatal("n still cancelled")
	}
	h.key("c", "y")
	if !h.api.Called("cancel ") {
		t.Fatalf("y did not cancel: %v", h.api.CallList())
	}
	h.poll()
	h.expect("cancelled, 7.6 GB kept")
	h.expect(setupCancelled)
	h.expect("R resume all")
	// The overall bar keeps its value.
	h.expect("7.6/29.6 GB")

	runs := countPrefix(h.api.CallList(), "run")
	h.key("R")
	if countPrefix(h.api.CallList(), "run") != runs+1 {
		t.Fatalf("Resume did not run setup again: %v", h.api.CallList())
	}
	before := 7.6
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 8e9, BytesTotal: total(qwen), DownloadedBeforeGB: &before})
	h.poll()
	h.focusOnProgress(qwen)
	h.expect("Resumed: 7.6 GB was already on this PC.")
}

func (h *harness) focusOnProgress(id string) {
	for i, r := range h.m.shownRows() {
		if sameID(r.ID, id) {
			h.m.focus = i
		}
	}
}

func countPrefix(calls []string, prefix string) int {
	n := 0
	for _, c := range calls {
		if strings.HasPrefix(c, prefix) {
			n++
		}
	}
	return n
}

// A small cancel (under 1 GB in) goes straight through.
func TestSmallCancelDoesNotAsk(t *testing.T) {
	h := startedDownload(t)
	h.key("c")
	if !h.api.Called("cancel") {
		t.Fatal("a cancel under 1 GB should not ask")
	}
}

// The daemon drops mid-stream: never a stale bar, a reconnect that works.
func TestLostContactNeverShowsStaleProgress(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 23e9, BytesTotal: total(qwen)})
	h.poll()
	h.api.SetStatusErr(errDropped)
	h.poll()
	h.expect("Lost contact with GAIA: connection refused. Press r to reconnect.")
	h.expect("last known: downloading")
	h.expectNot("Overall")
	h.expectNot("%")
	if busy, _ := h.m.Downloading(); busy {
		t.Fatal("a lost download must not report progress to the status line")
	}
	// No hidden retry: nothing polls until the user asks.
	n := countPrefix(h.api.CallList(), "status")
	h.tick(5 * time.Second)
	if countPrefix(h.api.CallList(), "status") != n {
		t.Fatal("polled again without the user pressing r")
	}
	h.api.SetStatusErr(nil)
	h.key("r")
	h.expectNot("Lost contact")
	h.expect("Overall")
}

func TestNetworkDropWithKeepTryingShowsTheWait(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.key("t")
	h.expect("[x] " + keepTryingLabel)
	h.key("enter")
	if !api.Called("run cloud=false ack=false keep=true") {
		t.Fatalf("keep trying was not sent: %v", api.CallList())
	}
	until := h.clock.Add(90 * time.Second).Unix()
	api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 1e9, BytesTotal: total(qwen), RetryingUntil: &until})
	h.poll()
	h.expect("connection dropped · retrying")
}

func TestFailuresSayWhatHappenedAndRetryWorks(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateFailed, BytesDone: 14_200_000_000, BytesTotal: total(qwen), ErrorKind: strp("disk_full")})
	h.api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.focusOnProgress(qwen)
	h.expect("Ran out of disk space on C: after 14.2 GB. Free 9.1 GB and press r to retry.")
	h.key("r")
	if !h.api.Called("download " + qwen) {
		t.Fatalf("retry did not download the model again: %v", h.api.CallList())
	}

	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateFailed, ErrorKind: strp("verify"), BytesTotal: total(gemma)})
	h.poll()
	h.focusOnProgress(gemma)
	h.expect("The file didn't pass its check. Press r to download it again.")

	h.api.Progress(setupapi.ModelProgress{ID: emb, State: setupapi.StateFailed, ErrorKind: strp("network"), BytesTotal: total(emb)})
	h.poll()
	h.focusOnProgress(emb)
	h.expect("The connection dropped. Press r to retry; the download continues from where it stopped.")
}

func TestRetryWhileSetupRunsSaysWhy(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateFailed, ErrorKind: strp("network"), BytesTotal: total(qwen)})
	h.poll()
	h.focusOnProgress(qwen)
	h.key("r")
	h.expect(runOwnsRetry)
}

// Reaching 100% waits for every row to be ready, then shows the summary.
func TestReadySummaryWaitsForEveryRow(t *testing.T) {
	h := startedDownload(t)
	for _, id := range []string{emb, qwen} {
		h.api.Progress(setupapi.ModelProgress{ID: id, State: setupapi.StateReady, BytesDone: total(id), BytesTotal: total(id)})
	}
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateVerifying, BytesDone: total(gemma), BytesTotal: total(gemma)})
	h.poll()
	if h.m.Screen() != "download" {
		t.Fatal("jumped to done with a model still verifying")
	}
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateReady, BytesDone: total(gemma), BytesTotal: total(gemma)})
	h.api.SetRunState(setupapi.RunReady)
	h.poll()
	if h.m.Screen() != "ready" {
		t.Fatalf("screen = %s, want ready", h.m.Screen())
	}
	h.expect("Chat       Qwen3.6-35B-A3B   (on this PC)")
	h.expect("Vision     Gemma-4-E4B-it")
	h.expect("Documents  embeddinggemma-300m")
	h.expect("enter start chatting")
}

func TestSkipAFailedModelUnblocksTheSummary(t *testing.T) {
	api := &f.Fake{P: warmPlan(), G: discreteGPU()}
	h := newHarness(t, ModeModels, api)
	h.focusID(oss)
	h.key("space", "a")
	api.Progress(setupapi.ModelProgress{ID: oss, State: setupapi.StateFailed, ErrorKind: strp("lemonade"), BytesTotal: 12e9})
	h.poll()
	h.focusOnProgress(oss)
	h.expect("s skip this model")
	h.key("s")
	h.expectNot("gpt-oss-20b")
}

// Two windows: a run already going is attached to, never started again.
func TestReopeningAttachesToARunningDownload(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	api.S = setupapi.Status{State: setupapi.RunRunning, Models: []setupapi.ModelProgress{
		{ID: qwen, State: setupapi.StateDownloading, BytesDone: 5e9, BytesTotal: total(qwen)},
	}}
	h := newHarness(t, ModeFirstRun, api)
	if h.m.Screen() != "download" {
		t.Fatalf("screen = %s, want download", h.m.Screen())
	}
	if api.Called("run") {
		t.Fatal("reopening restarted the run")
	}
	h.expect("5.0/23.3 GB")
}

// A status read sent before a new download started is never taken for it.
func TestAStaleStatusIsIgnored(t *testing.T) {
	h := startedDownload(t)
	stale := setupapi.Status{State: setupapi.RunCancelled}
	h.send(statusMsg{st: stale, issued: h.clock.Add(-time.Hour), at: h.clock})
	if h.m.Screen() != "download" || !h.m.dl.active {
		t.Fatal("a stale read ended the download")
	}
}

// --- /models -----------------------------------------------------------------

func TestModelsAppliesDownloadsOneAtATimeAndKeepsGoingWhenClosed(t *testing.T) {
	api := &f.Fake{P: warmPlan(), G: discreteGPU()}
	h := newHarness(t, ModeModels, api)
	if h.m.Screen() != "models" {
		t.Fatalf("screen = %s", h.m.Screen())
	}
	h.expect("Models on this PC")
	h.expect("a apply")
	h.focusID(oss)
	h.key("space")
	h.expect("Download 12.1 GB")
	h.key("a")
	if !api.Called("download " + oss) {
		t.Fatalf("apply did not download: %v", api.CallList())
	}
	if api.Called("run") {
		t.Fatal("/models must not run all of setup")
	}
	h.expect(leavingKeeps)
	_, cmd := h.m.Update(keyMsg("esc"))
	var closed *ClosedMsg
	for _, msg := range collect(cmd) {
		if c, ok := msg.(ClosedMsg); ok {
			closed = &c
		}
	}
	if closed == nil || !closed.Downloading {
		t.Fatalf("closing during a download must say it keeps going: %+v", closed)
	}
}

func TestNothingIsDeletedWithoutRemoveAndConfirm(t *testing.T) {
	api := &f.Fake{P: warmPlan(), G: discreteGPU()}
	h := newHarness(t, ModeModels, api)
	h.focusID(gemma)
	h.key("space", "a")
	if api.Called("remove") {
		t.Fatal("unticking deleted a model")
	}
	if !api.Called("skip " + gemma + " true") {
		t.Fatalf("unticking should only skip: %v", api.CallList())
	}
	h.focusID(gemma)
	h.key("x")
	h.expect("Remove Gemma-4-E4B-it? This frees 6.0 GB and turns off vision. You can download it again.")
	h.key("n")
	if api.Called("remove") {
		t.Fatal("n removed it")
	}
	h.key("x", "y")
	if !api.Called("remove " + gemma) {
		t.Fatalf("y did not remove: %v", api.CallList())
	}
}

func TestApplyShowsTheSameWarningsAsSetup(t *testing.T) {
	p := small16()
	p.Disk.FreeGB = f.Float(10)
	api := &f.Fake{P: p, G: discreteGPU()}
	h := newHarness(t, ModeModels, api)
	h.focusID(qwen)
	h.key("space", "a")
	h.expect("Needs ~27 GB, this PC has 16 GB.")
	h.key("y")
	h.expect("Needs 29.6 GB; only 10.0 GB free on C:.")
	h.key("y")
	if !api.Called("download") {
		t.Fatalf("confirmed apply did not download: %v", api.CallList())
	}
}

func TestLoadFailureOffersTryAgainAndTheRecommendedSet(t *testing.T) {
	api := &f.Fake{PlanErr: errDropped, G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.expect("Couldn't read the model list from Lemonade: connection refused.")
	h.expect("r try again")
	h.expect("u use the recommended set")
	api.PlanErr = nil
	api.P = coldPlan()
	h.key("r")
	if h.m.Screen() != "models" {
		t.Fatalf("try again did not reload: %s", h.m.Screen())
	}
}

func TestNoServerOffersInstallOnlyWhenTheHostCan(t *testing.T) {
	p := coldPlan()
	p.Lemonade.Reachable = false
	h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})
	h.expect(titleNoServer)
	h.expectNot("enter install it")

	h2 := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()}, func(o *Options) { o.OfferInstall = true })
	h2.expect("enter install it")
	_, cmd := h2.m.Update(keyMsg("enter"))
	var asked bool
	for _, msg := range collect(cmd) {
		if _, ok := msg.(InstallServerMsg); ok {
			asked = true
		}
	}
	if !asked {
		t.Fatal("enter did not ask the host to install the server")
	}
}

func strp(s string) *string { return &s }

// After setup saves a chat model, the plan stops listing Gemma, which stays on
// this PC for vision. It must still show as the vision model, ticked, and
// unticking it can't send a skip the backend would refuse.
func TestAVisionModelSetupNoLongerPlansStaysVisible(t *testing.T) {
	p := warmPlan()
	for i := range p.Catalog {
		if p.Catalog[i].ID == gemma {
			p.Catalog[i].Planned, p.Catalog[i].Selected = false, false
		}
	}
	api := &f.Fake{P: p, G: discreteGPU()}
	h := newHarness(t, ModeModels, api)
	h.expect("[x] Gemma-4-E4B-it")
	h.expectNot("more already in Lemonade")
	h.focusID(gemma)
	h.key("space")
	h.expect("[x] Gemma-4-E4B-it")
	h.expect("nothing to untick")
	h.key("a")
	if api.Called("skip") {
		t.Fatalf("a skip was sent for a model setup no longer plans: %v", api.CallList())
	}
	h.expect("Nothing to apply.")
}

// Resuming: the new run lists a half-downloaded model as queued with no bytes
// until its pull restarts. The overall bar keeps what is on disk.
func TestResumeNeverDropsTheOverallBar(t *testing.T) {
	h := startedDownload(t)
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateDownloading, BytesDone: 2_300_000_000, BytesTotal: total(gemma)})
	h.poll()
	h.key("c", "y")
	h.poll()
	h.expect("2.3/29.6 GB")
	h.key("R")
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateQueued, BytesTotal: total(gemma)})
	h.poll()
	h.expect("2.3/29.6 GB")
	h.expect("queued, 2.3 GB already here")
}

// Over the free space, the totals line says how much is missing rather than
// claiming 0 MB would be left.
func TestTotalsLineSaysHowShortTheDiskIs(t *testing.T) {
	p := coldPlan()
	p.Disk.FreeGB = f.Float(10)
	h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})
	h.expect("Download 29.6 GB · free on C: 10.0 GB · 19.6 GB short")
	h.expectNot("after 0 MB")
}

// The warning words survive a narrow mark column; the recommendation shrinks.
func TestUnknownFitKeepsItsWordsWhenTheRowIsTight(t *testing.T) {
	p := coldPlan()
	for i := range p.Catalog {
		p.Catalog[i].Fits, p.Catalog[i].NeedGB, p.Catalog[i].CapacityGB = nil, nil, nil
	}
	h := newHarness(t, ModeFirstRun, &f.Fake{P: p, G: discreteGPU()})
	for _, l := range strings.Split(h.screen(), "\n") {
		if strings.Contains(l, "Gemma-4-E4B-it ") && !strings.Contains(l, "[?] fit unknown *") {
			t.Fatalf("row lost its words: %q", l)
		}
	}
}

// The count carries on from the host's checklist: after "where chat runs" and
// "install the server", the models are step 3.
func TestStepsCarryOnFromTheHost(t *testing.T) {
	h := newHarness(t, ModeFirstRun, &f.Fake{P: coldPlan(), G: gpuShared()},
		func(o *Options) { o.StepsBefore = 2 })
	h.expect("step 3 of 5")
	h.key("enter")
	h.expect("step 4 of 5")
	h.key("s")
	h.expect("step 5 of 5")
}

// A single pull stops at its next chunk, after the cancel call returns. The
// screen keeps reading until the backend says it stopped, and never leaves a
// frozen "downloading" row behind.
func TestCancellingASinglePullWaitsForItToStop(t *testing.T) {
	api := &f.Fake{P: warmPlan(), G: discreteGPU(), CancelLater: true}
	h := newHarness(t, ModeModels, api)
	h.focusID(oss)
	h.key("space", "a")
	api.Progress(setupapi.ModelProgress{ID: oss, State: setupapi.StateDownloading, BytesDone: 2e9, BytesTotal: 12e9})
	h.poll()
	h.key("c", "y")
	// The backend still reports it downloading for a moment.
	api.Progress(setupapi.ModelProgress{ID: oss, State: setupapi.StateDownloading, BytesDone: 2_100_000_000, BytesTotal: 12e9})
	h.poll()
	if !h.m.dl.active {
		t.Fatal("stopped following a pull that has not stopped yet")
	}
	h.expect("stopping…")
	api.Progress(setupapi.ModelProgress{ID: oss, State: setupapi.StateCancelled, BytesDone: 2_100_000_000, BytesTotal: 12e9})
	h.poll()
	if h.m.dl.active {
		t.Fatal("still active after the pull stopped")
	}
	h.expect("cancelled, 2.1 GB kept")
}

// A partly downloaded model shows what is left in full.
func TestPartlyDownloadedSizeIsNotCut(t *testing.T) {
	p := warmPlan()
	for i := range p.Catalog {
		if p.Catalog[i].ID == oss {
			left := 10.8
			p.Catalog[i].SizeGB, p.Catalog[i].Partial = &left, true
		}
	}
	h := newHarness(t, ModeModels, &f.Fake{P: p, G: discreteGPU()})
	h.expect("10.8 GB left")
}

// Found driving the real daemon: a status read sent while the retry was still
// being started showed the model as failed, and the screen took that for the
// retry's result and stopped following a download that went on to finish.
func TestAReadFromBeforeTheRetryStartedIsNotItsResult(t *testing.T) {
	h := startedDownload(t)
	errText := "gaia init failed"
	for _, id := range []string{emb, qwen} {
		h.api.Progress(setupapi.ModelProgress{ID: id, State: setupapi.StateReady, BytesDone: total(id), BytesTotal: total(id)})
	}
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateFailed, BytesDone: 5e8, BytesTotal: total(gemma), ErrorKind: strp("disk_full")})
	h.api.S.State, h.api.S.Error = setupapi.RunFailed, &errText
	h.poll()
	h.focusOnProgress(gemma)

	// Press r, but hold the backend's answer back.
	var cmd tea.Cmd
	h.m, cmd = h.m.Update(keyMsg("r"))
	stale := setupapi.Status{State: setupapi.RunFailed, Models: []setupapi.ModelProgress{
		{ID: gemma, State: setupapi.StateFailed, BytesDone: 5e8, BytesTotal: total(gemma)},
	}}
	h.send(statusMsg{st: stale, issued: h.clock, at: h.clock})
	if !h.m.dl.active {
		t.Fatal("a read from before the retry started ended it")
	}
	h.run(cmd)
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateDownloading, BytesDone: 1e9, BytesTotal: total(gemma)})
	h.poll()
	if !h.m.dl.active {
		t.Fatal("not following the retried download")
	}
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateReady, BytesDone: total(gemma), BytesTotal: total(gemma)})
	h.poll()
	if h.m.Screen() != "ready" {
		t.Fatalf("screen = %s, want ready once the retry finished", h.m.Screen())
	}
}

// A load-check failure says which case it is, in the backend's words.
func TestALoadCheckFailureUsesTheBackendsWords(t *testing.T) {
	h := startedDownload(t)
	msg := "Setup stopped before GAIA could check that Gemma-4-E4B-it-GGUF loads. Fix the error above, then run setup again."
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateFailed, ErrorKind: strp("verify"), Error: &msg, BytesTotal: total(gemma)})
	h.api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.focusOnProgress(gemma)
	h.expect("Setup stopped before GAIA could check that Gemma-4-E4B-it-GGUF loads.")
	h.expect("Press r to try again.")
}

// When a run ends with failures, the cursor lands on the one that caused it
// (the full disk), not on a model the run merely stopped before checking.
func TestTheCursorLandsOnTheRootCause(t *testing.T) {
	h := startedDownload(t)
	stopped := "Setup stopped before GAIA could check that it loads."
	h.api.Progress(setupapi.ModelProgress{ID: emb, State: setupapi.StateFailed, ErrorKind: strp("verify"), Error: &stopped, BytesTotal: total(emb)})
	h.api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateFailed, ErrorKind: strp("disk_full"), BytesDone: 2e9, BytesTotal: total(qwen)})
	h.api.Progress(setupapi.ModelProgress{ID: gemma, State: setupapi.StateFailed, ErrorKind: strp("verify"), Error: &stopped, BytesTotal: total(gemma)})
	h.api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.expect("Ran out of disk space on C:")
}
