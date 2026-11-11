// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/amd/gaia/tui/internal/setupapi"
	f "github.com/amd/gaia/tui/internal/setupapi/setupapitest"
)

const (
	vl     = "Qwen3-VL-4B-Instruct-GGUF"
	whisp  = "Whisper-Large-v3-Turbo"
	kokoro = "kokoro-v1"
	sdxl   = "SDXL-Turbo"
)

// bigPC is a 64 GB PC as the backend describes it: the recommended chat model
// reads images itself, Gemma is the compact one, gpt-oss can't read images, and
// the extras are listed but not planned.
func bigPC() setupapi.Plan {
	p := coldPlan()
	p.Catalog = []setupapi.CatalogEntry{
		f.Entry(qwen, chat, 23.3, false, true, true, f.Bool(true), 27.1, 64),
		f.Entry(gemma, vision, 5.97, false, true, true, f.Bool(true), 8.4, 64),
		f.Entry(emb, embed, 0.33, false, true, true, f.Bool(true), 0.6, 64),
		f.Entry(oss, chat, 12.1, false, false, false, f.Bool(true), 14.0, 64),
		f.Entry(vl, vision, 3.1, false, false, false, f.Bool(true), 4.0, 64),
		f.Entry(whisp, []string{setupapi.RoleSpeech}, 1.6, false, false, false, f.Bool(true), 2, 64),
		f.Entry(kokoro, []string{setupapi.RoleVoice}, 0.35, false, false, false, f.Bool(true), 1, 64),
		f.Entry(sdxl, []string{setupapi.RoleImageCreation}, 6.5, false, false, false, f.Bool(true), 8, 64),
	}
	for i := range p.Catalog {
		e := &p.Catalog[i]
		e.Source = f.Str("huggingface.co/x/" + e.Name)
		e.FeatureTier = "full"
		switch e.ID {
		case qwen, gemma:
			e.ReadsImages = true
		case vl, whisp, kokoro, sdxl:
			e.ChatChoice = false
		}
		if e.ID == gemma {
			e.FeatureTier = setupapi.TierCompact
		}
	}
	p = labelled(p)
	for i := range p.Catalog {
		switch p.Catalog[i].ID {
		case whisp, kokoro, sdxl:
			p.Catalog[i].Support = nil
		}
	}
	p.Components = []setupapi.Component{
		{ID: "server", SizeGB: f.Float(0.007), Source: "github.com/lemonade-sdk/lemonade", Installed: f.Bool(true)},
		{ID: "backend", SizeGB: f.Float(0.141), Source: "github.com/ggml-org/llama.cpp"},
		{ID: "packages", Source: "pypi.org"},
		{ID: "agent", Source: "hub.amd-gaia.ai", Installed: f.Bool(false)},
	}
	return p
}

// bigHarness opens setup in a window tall enough for every job.
func bigHarness(t *testing.T, api *f.Fake) *harness {
	t.Helper()
	h := newHarness(t, ModeFirstRun, api)
	h.send(tea.WindowSizeMsg{Width: 100, Height: 40})
	return h
}

func ticked(h *harness, id string) bool { return h.m.sel.ticked[id] }

// Every job has its heading, chat models carry Verified or Untested, and only
// the one the PC should use reads Recommended.
func TestModelsAreGroupedByTheJobTheyDo(t *testing.T) {
	h := bigHarness(t, &f.Fake{P: bigPC(), G: discreteGPU()})
	s := h.screen()
	last := -1
	for _, heading := range []string{jobChatTitle, jobVisionTitle, jobDocsTitle, jobExtrasTitle} {
		at := strings.Index(s, heading)
		if at <= last {
			t.Fatalf("%q out of order:\n%s", heading, s)
		}
		last = at
	}
	h.expect("(•) Qwen3.6-35B-A3B " + labelVerified)
	h.expect("( ) gpt-oss-20b " + labelUntested)
	if n := strings.Count(s, "* recommended"); n != 2 {
		t.Fatalf("recommended on %d rows, want chat and documents:\n%s", n, s)
	}
	// Verified chat models come before Untested ones.
	if strings.Index(s, "Gemma-4-E4B-it") > strings.Index(s, "gpt-oss-20b") {
		t.Fatalf("an Untested model sits above a Verified one:\n%s", s)
	}
}

func TestOptionalExtrasAreListedByJobAndOffByDefault(t *testing.T) {
	h := bigHarness(t, &f.Fake{P: bigPC(), G: discreteGPU()})
	h.expect(jobExtrasTitle + " (" + extrasOff + ")")
	for _, label := range []string{"Speech to text", "Text to speech", "Creating images"} {
		h.expect("[ ] " + label)
	}
	for _, id := range []string{whisp, kokoro, sdxl} {
		if ticked(h, id) {
			t.Errorf("%s is ticked by default", id)
		}
	}
}

// A chat model that reads images needs no second model; one that can't gets
// Gemma back, and the choice replaces the old chat model's download.
func TestPickingAChatModelUpdatesTheVisionJob(t *testing.T) {
	h := bigHarness(t, &f.Fake{P: bigPC(), G: discreteGPU()})
	if ticked(h, gemma) {
		t.Fatal("Gemma is ticked though the chat model reads images")
	}
	h.expect("Handled by Qwen3.6-35B-A3B · Nothing more to download")
	h.expect(otherVision)

	h.focusID(oss)
	h.key("space")
	if !ticked(h, gemma) {
		t.Fatal("a chat model that can't read images must preselect Gemma")
	}
	if ticked(h, qwen) || !ticked(h, oss) || h.m.sel.chat != oss {
		t.Fatalf("the pick did not replace the chat model: %+v", h.m.sel.ticked)
	}
	h.expect("gpt-oss-20b can't read images, so we picked a model for that job.")
	h.expect("(•) Gemma-4-E4B-it")
	h.expect("[!] " + untestedPicked)
	// Downloading only what the choice uses: gpt-oss, Gemma, the embedder.
	h.expect("Download 18.4 GB")

	h.focusID(qwen)
	h.key("space")
	if ticked(h, gemma) || ticked(h, oss) {
		t.Fatalf("back on a chat model that reads images, Gemma and gpt-oss stay out: %+v", h.m.sel.ticked)
	}
}

// A model already on disk stays put when the pick moves off it.
func TestPickingAwayFromADownloadedChatModelKeepsIt(t *testing.T) {
	p := bigPC()
	yes := true
	zero := 0.0
	p.Catalog[0].Downloaded, p.Catalog[0].SizeGB = &yes, &zero
	h := bigHarness(t, &f.Fake{P: p, G: discreteGPU()})
	h.focusID(oss)
	h.key("space")
	if !ticked(h, qwen) {
		t.Fatal("picking another chat model unticked one already on this PC")
	}
}

func TestACompactChatModelCantTakeImageCreation(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.focusID(sdxl)
	h.key("space")
	if !ticked(h, sdxl) {
		t.Fatal("a full-tier chat model can take image creation")
	}

	h.focusID(gemma)
	h.key("space")
	if ticked(h, sdxl) {
		t.Fatal("a compact chat model kept image creation ticked")
	}
	h.expect("[!] " + notWithThisChat)
	h.focusID(sdxl)
	h.key("space")
	if ticked(h, sdxl) {
		t.Fatal("image creation was ticked under a compact chat model")
	}
	h.expect(imagesBlockedLine("Gemma-4-E4B-it"))
	h.key("enter")
	if strings.Contains(strings.Join(api.CallList(), " "), sdxl) {
		t.Fatalf("image creation reached the backend: %v", api.CallList())
	}
}

// Another vision model is behind one line while the chat model reads images,
// and the pick is saved through its own call.
func TestChoosingAnotherVisionModelIsSaved(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.expectNot("Qwen3-VL-4B")
	h.focusAction(actionOtherVision)
	h.key("space")
	h.expect("Qwen3-VL-4B")
	h.expect(hideVision)
	h.focusJob(jobVision, vl)
	h.key("space")
	if !ticked(h, vl) {
		t.Fatal("the picked vision model is not ticked")
	}
	h.key("enter")
	if !api.Called("vision " + vl) {
		t.Fatalf("the vision pick was not saved: %v", api.CallList())
	}
	if !api.Called("run cloud=false ack=false keep=false extras=" + vl) {
		t.Fatalf("the vision model is outside the plan, so the run must fetch it: %v", api.CallList())
	}
}

// GAIA's own choice saves nothing, so a later chat model keeps following it.
func TestGAIAsOwnVisionChoiceIsNotSaved(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.focusID(oss)
	h.key("space", "enter")
	if api.Called("vision") {
		t.Fatalf("GAIA's choice was saved as the user's: %v", api.CallList())
	}
}

func TestASavedVisionModelShowsAsPicked(t *testing.T) {
	p := bigPC()
	p.VisionModel = f.Str(vl)
	h := bigHarness(t, &f.Fake{P: p, G: discreteGPU()})
	if got := h.m.sel.resolveVision(); got != vl {
		t.Fatalf("vision = %q, want the saved %q", got, vl)
	}
	if !ticked(h, vl) {
		t.Fatal("the saved vision model is not ticked")
	}
}

func TestAVisionChoiceTheBackendRefusesIsSaidOutLoud(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU(), VisionErr: &setupapi.StatusError{Op: "choose", Status: 404, Detail: "no such route"}}
	h := bigHarness(t, api)
	h.focusAction(actionOtherVision)
	h.key("space")
	h.focusJob(jobVision, vl)
	h.key("space", "enter")
	if api.Called("run") {
		t.Fatalf("setup started without the choice saved: %v", api.CallList())
	}
	h.expect("Could not save your choices: no such route")
}

// Extras download inside setup's run, so gaia init tests them too.
func TestExtrasAreFetchedAndTestedByTheRun(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.focusID(whisp)
	h.key("space", "enter")
	if !api.Called("run cloud=false ack=false keep=false extras=" + whisp) {
		t.Fatalf("the extra was not sent to the run: %v", api.CallList())
	}
	if api.Called("download") {
		t.Fatalf("an extra must not download outside the run: %v", api.CallList())
	}
}

// /models shows each model once, ticked, under the same groups.
func TestModelsCommandKeepsItsTicksUnderTheSameGroups(t *testing.T) {
	h := newHarness(t, ModeModels, &f.Fake{P: bigPC(), G: discreteGPU()})
	h.send(tea.WindowSizeMsg{Width: 100, Height: 40})
	h.expect(jobChatTitle)
	h.expect(jobVisionTitle)
	h.expect("[x] Qwen3.6-35B-A3B")
	h.expect("Default chat: Qwen3.6-35B-A3B (c to change)")
	h.expectNot(otherVision)
	if n := strings.Count(h.screen(), "Gemma-4-E4B-it"); n != 1 {
		t.Fatalf("/models lists Gemma %d times:\n%s", n, h.screen())
	}
	// Ticking keeps the chat model and adds a download beside it.
	h.focusID(oss)
	h.key("space")
	if h.m.sel.chat != qwen || !ticked(h, qwen) || !ticked(h, oss) {
		t.Fatalf("a tick moved the chat model: %+v", h.m.sel)
	}
}

// --- what comes to this PC ---------------------------------------------------

func TestEverythingComingIsListedBeforeAnythingStarts(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.expect("Also downloads Model runtime, Python packages, GAIA agent.")
	h.key("v")
	if h.m.Screen() != "contents" {
		t.Fatalf("screen = %s, want contents", h.m.Screen())
	}
	h.expect(titleContents)
	h.expect("GAIA's model server")
	h.expect(contentsHere + " · 7 MB · From github.com/lemonade-sdk/lemonade")
	h.expect("Model runtime")
	h.expect(contentsWaiting + " · 141 MB · From github.com/ggml-org/llama.cpp")
	h.expect("Python packages")
	h.expect(contentsSizeLater + " · From pypi.org")
	h.expect("GAIA agent")
	h.expect("Qwen3.6-35B-A3B")
	h.expect("23.3 GB · From huggingface.co/x/Qwen3.6-35B-A3B")
	if api.Called("run") {
		t.Fatal("listing what comes must not start it")
	}
	h.key("esc")
	if h.m.Screen() != "models" {
		t.Fatalf("esc went to %s", h.m.Screen())
	}
	h.key("v", "enter")
	if !api.Called("run") {
		t.Fatalf("enter did not continue: %v", api.CallList())
	}
}

// Components have no bytes to report, so they get states; models keep their bar.
func TestTheDownloadListsComponentsWithLiveStates(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.key("enter")
	api.SetComponent("server", setupapi.ComponentDone)
	api.SetComponent("backend", setupapi.ComponentActive)
	api.SetComponent("packages", setupapi.ComponentFailed)
	api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateDownloading, BytesDone: 5e9, BytesTotal: 23_300_000_000})
	api.Progress(setupapi.ModelProgress{ID: emb, State: setupapi.StateQueued, BytesTotal: 330_000_000})
	h.poll()
	h.expect("[ok] GAIA's model server " + contentsReady + " · 7 MB")
	h.expect("[..] Model runtime " + contentsWorking + " · 141 MB")
	h.expect("[!] Python packages " + contentsFailed)
	h.expect("[ ] GAIA agent " + contentsWaiting)
	h.expect("5.0/23.3 GB")
	h.expect("queued · 330 MB · From huggingface.co/x/embeddinggemma-300m")
}

// /models downloads models alone, so it names no server or runtime.
func TestModelsCommandListsNoComponents(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := newHarness(t, ModeModels, api)
	h.focusID(oss)
	h.key("space", "a")
	h.expectNot(componentName("server"))
}

// A window too short for the list keeps the rows the user acts on.
func TestAShortWindowDropsComponentsBeforeModels(t *testing.T) {
	api := &f.Fake{P: bigPC(), G: discreteGPU()}
	h := bigHarness(t, api)
	h.key("enter")
	h.send(tea.WindowSizeMsg{Width: 80, Height: 12})
	h.expect("Qwen3.6-35B-A3B")
	h.expectNot(componentName("backend"))
}

// --- the Test step -----------------------------------------------------------

func row(job, subject, state string) setupapi.TestRow {
	r := setupapi.TestRow{Job: job, State: state}
	if subject != "" {
		r.Subject = &subject
	}
	return r
}

func failed(job, subject, kind, why string) setupapi.TestRow {
	r := row(job, subject, setupapi.TestFailed)
	r.Kind, r.Error = &kind, &why
	return r
}

// atTheTestStep is setup testing, with every model on disk.
func atTheTestStep(t *testing.T) (*harness, *f.Fake) {
	t.Helper()
	api := &f.Fake{P: warmPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.send(tea.WindowSizeMsg{Width: 100, Height: 40})
	h.key("enter")
	api.SetTest(row(setupapi.JobServer, "", setupapi.TestPassed))
	h.poll()
	if h.m.Screen() != "test" {
		t.Fatalf("screen = %s, want test", h.m.Screen())
	}
	return h, api
}

func TestEveryTestRowShowsAsItRuns(t *testing.T) {
	h, api := atTheTestStep(t)
	h.expect(titleTest)
	h.expect("[ok] Model server answers")
	h.expect("[ ] A real chat with GAIA")
	h.expect("Tests done: 1 of 5")
	secs := 12.4
	passed := row(setupapi.RoleEmbedding, emb, setupapi.TestPassed)
	passed.Seconds = &secs
	api.SetTest(passed)
	api.SetTest(row(setupapi.RoleChat, qwen, setupapi.TestRunning))
	h.poll()
	h.expect("Documents and memory: makes an embedding · embeddinggemma-300m")
	h.expect("Passed, 12 s")
	h.expect("[..] Chat: loads and answers · Qwen3.6-35B-A3B")
	h.expect(testRunning)
	h.expect("Tests done: 2 of 5")
	h.expect("c cancel")
}

// The reason this step exists: a required failure holds "ready", with the real
// error and a key, and nothing to type.
func TestARequiredFailureBlocksReadyWithTheRealErrorAndARetry(t *testing.T) {
	h, api := atTheTestStep(t)
	why := "Lemonade answered 500: llama-server exited"
	api.SetTest(failed(setupapi.RoleChat, qwen, setupapi.KindBroken, why))
	api.SetRunState(setupapi.RunFailed)
	h.api.SetRunError(false, "`gaia init --profile gaia --yes` failed (exit 1): "+why)
	h.poll()
	if h.m.Screen() != "test" {
		t.Fatalf("screen = %s, want test: ready must wait for the tests", h.m.Screen())
	}
	h.expect(why)
	h.expect(testFailedHelp)
	h.expect("r test again")
	h.expect("m choose another model")
	h.expect("x download again")
	h.expect(downloadAgain("Qwen3.6-35B-A3B"))
	h.expectNot("gaia init")
	h.expectNot("start chatting")

	h.key("enter")
	if h.m.Screen() != "test" {
		t.Fatalf("enter moved past a failed test to %s", h.m.Screen())
	}

	before := countPrefix(api.CallList(), "run")
	h.key("r")
	if countPrefix(api.CallList(), "run") != before+1 {
		t.Fatalf("r did not test again: %v", api.CallList())
	}
	h.expectNot(why)
	api.SetTest(row(setupapi.RoleChat, qwen, setupapi.TestPassed))
	h.poll()
	h.expect("[ok] Chat: loads and answers")
	api.SetRunState(setupapi.RunReady)
	h.poll()
	if h.m.Screen() != "ready" {
		t.Fatalf("screen = %s, want ready after the retest passed", h.m.Screen())
	}
}

func TestShortOfMemorySaysWhatToDoAndOffersNoNewCopy(t *testing.T) {
	h, api := atTheTestStep(t)
	api.SetTest(failed(setupapi.RoleChat, qwen, setupapi.KindMemory, "Needs 27 GB free; 9 GB are."))
	api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.expect("Needs 27 GB free; 9 GB are.")
	h.expect(testFailedMem)
	h.expectNot("x download again")
	h.key("x")
	if h.m.ask != askNone {
		t.Fatal("a new copy doesn't help a PC short of memory")
	}
}

func TestChooseAnotherModelGoesBackToTheChoice(t *testing.T) {
	h, api := atTheTestStep(t)
	api.SetTest(failed(setupapi.RoleChat, qwen, setupapi.KindStuck, "It didn't finish within 5 minutes"))
	api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.key("m")
	if h.m.Screen() != "models" {
		t.Fatalf("m went to %s", h.m.Screen())
	}
}

// Downloading a broken model again goes through the confirm, then back through
// the run: a new copy proves nothing until it is tested.
func TestDownloadAgainReplacesTheModelThenTestsIt(t *testing.T) {
	api := &f.Fake{P: warmPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.key("enter")
	api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateReady, BytesDone: 1, BytesTotal: 1})
	api.SetTest(failed(setupapi.RoleChat, qwen, setupapi.KindBroken, "would not load"))
	api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.poll()
	h.key("x")
	h.expect(redownloadAsk("Qwen3.6-35B-A3B"))
	if api.Called("remove") {
		t.Fatal("deleted before the user confirmed")
	}
	h.key("y")
	if !api.Called("remove "+qwen) || !api.Called("download "+qwen) {
		t.Fatalf("download again: %v", api.CallList())
	}
	if h.m.Screen() != "download" {
		t.Fatalf("screen = %s, want download", h.m.Screen())
	}
	before := countPrefix(api.CallList(), "run")
	api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateReady, BytesDone: 1, BytesTotal: 1})
	h.poll()
	if countPrefix(api.CallList(), "run") != before+1 {
		t.Fatalf("the new copy was not tested: %v", api.CallList())
	}
}

// All the models pass and the run still fails: the real chat didn't work.
func TestAFailedAgentTurnBlocksReadyThoughEveryModelPassed(t *testing.T) {
	h, api := atTheTestStep(t)
	why := "GAIA didn't answer a test message within 5 minutes."
	api.SetTest(row(setupapi.RoleChat, qwen, setupapi.TestPassed))
	api.SetTest(failed(setupapi.JobAgent, "", setupapi.KindBroken, why))
	api.SetRunState(setupapi.RunFailed)
	h.api.SetRunError(false, why)
	h.poll()
	if h.m.Screen() != "test" {
		t.Fatalf("screen = %s", h.m.Screen())
	}
	h.expect(why)
	if n := strings.Count(flat(h.screen()), why); n != 1 {
		t.Fatalf("the error shows %d times:\n%s", n, h.screen())
	}
	// No model to replace: an agent failure has no subject.
	h.expectNot("x download again")
}

// A run that fails with no test rows at all still can't read as ready, even
// with every model downloaded.
func TestARunThatFailedWithoutRowsIsNotReady(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.key("enter")
	for _, id := range []string{gemma, emb, qwen} {
		api.Progress(setupapi.ModelProgress{ID: id, State: setupapi.StateReady, BytesDone: 1, BytesTotal: 1})
	}
	api.SetRunState(setupapi.RunFailed)
	api.SetRunError(false, "Setup finished but GAIA is still not ready.")
	h.poll()
	h.poll()
	if h.m.Screen() != "test" {
		t.Fatalf("screen = %s, want the failure held on the Test step", h.m.Screen())
	}
	h.expect("Setup finished but GAIA is still not ready.")
}

// A run that reports ready with a required row failed is still blocked.
func TestReadyWithARequiredFailureIsNotBelieved(t *testing.T) {
	h, api := atTheTestStep(t)
	api.SetTest(failed(setupapi.RoleVision, gemma, setupapi.KindBroken, "said blue"))
	api.SetRunState(setupapi.RunReady)
	h.poll()
	if h.m.Screen() != "test" {
		t.Fatalf("screen = %s", h.m.Screen())
	}
}

// An optional extra failing is reported, but GAIA works without it.
func TestAnOptionalFailureDoesNotBlockReadyButIsReported(t *testing.T) {
	h, api := atTheTestStep(t)
	api.SetTest(failed(setupapi.RoleSpeech, whisp, setupapi.KindBroken, "no audio backend"))
	api.SetRunState(setupapi.RunReady)
	h.poll()
	if h.m.Screen() != "ready" {
		t.Fatalf("screen = %s, want ready", h.m.Screen())
	}
	h.expect("Speech to text didn't pass its test: no audio backend")
}

// A download that failed in the run is the download screen's to explain.
func TestADownloadFailureStaysOnTheDownloadScreen(t *testing.T) {
	api := &f.Fake{P: coldPlan(), G: discreteGPU()}
	h := newHarness(t, ModeFirstRun, api)
	h.key("enter")
	kind, msg := "network", "The connection dropped."
	api.Progress(setupapi.ModelProgress{ID: qwen, State: setupapi.StateFailed, ErrorKind: &kind, Error: &msg, BytesTotal: 1})
	api.SetTest(row(setupapi.JobServer, "", setupapi.TestPassed))
	api.SetRunState(setupapi.RunFailed)
	api.SetRunError(true, "exit 1")
	h.poll()
	h.poll()
	if h.m.Screen() != "download" {
		t.Fatalf("screen = %s, want download", h.m.Screen())
	}
}

func TestCancellingTheTestsLeavesItUnproven(t *testing.T) {
	h, api := atTheTestStep(t)
	h.key("c")
	h.poll()
	if s := h.m.Screen(); s == "ready" {
		t.Fatalf("a cancelled test read as ready")
	}
	if !api.Called("cancel") {
		t.Fatalf("c did not cancel: %v", api.CallList())
	}
}

// A lost daemon mid-test reconnects with r, and is never read as a failure.
func TestLosingContactDuringTheTestsReconnects(t *testing.T) {
	h, api := atTheTestStep(t)
	api.SetStatusErr(errDropped)
	h.poll()
	h.expect(lostContact)
	h.expect("r reconnect")
	api.SetStatusErr(nil)
	h.key("r")
	h.expectNot(lostContact)
	if h.m.Screen() != "test" || !h.m.dl.active {
		t.Fatalf("screen = %s active=%v", h.m.Screen(), h.m.dl.active)
	}
}

// Reopening setup while a run tests attaches to it, on the Test step.
func TestAttachingToARunningTestShowsTheTestStep(t *testing.T) {
	api := &f.Fake{P: warmPlan(), G: discreteGPU()}
	api.S = setupapi.Status{State: setupapi.RunVerifying}
	h := newHarness(t, ModeFirstRun, api)
	h.poll()
	if h.m.Screen() != "test" {
		t.Fatalf("screen = %s, want test", h.m.Screen())
	}
}

// At 80 columns every way on stays in the footer.
func TestTheFooterKeepsEveryWayOnAtEightyColumns(t *testing.T) {
	h, api := atTheTestStep(t)
	api.SetTest(failed(setupapi.RoleChat, qwen, setupapi.KindBroken, "would not load"))
	api.SetRunState(setupapi.RunFailed)
	h.poll()
	h.send(tea.WindowSizeMsg{Width: 80, Height: 24})
	for _, want := range []string{"m choose another model", "x download again", "r test again", "esc back"} {
		h.expect(want)
	}
}

func TestEveryScreenFitsWithTheNewRows(t *testing.T) {
	h, api := atTheTestStep(t)
	long := strings.Repeat("a long backend error ", 10)
	api.SetTest(failed(setupapi.RoleChat, qwen, setupapi.KindBroken, long))
	api.SetRunState(setupapi.RunFailed)
	h.poll()
	for _, sz := range [][2]int{{40, 10}, {60, 16}, {80, 24}, {120, 40}} {
		h.send(tea.WindowSizeMsg{Width: sz[0], Height: sz[1]})
		lines := strings.Split(h.screen(), "\n")
		if len(lines) > sz[1] {
			t.Fatalf("%dx%d: %d lines", sz[0], sz[1], len(lines))
		}
		if !strings.Contains(lines[len(lines)-1], "esc") {
			t.Fatalf("%dx%d: footer lost esc: %q", sz[0], sz[1], lines[len(lines)-1])
		}
	}
}
