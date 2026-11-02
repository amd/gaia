// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"fmt"
	"strings"

	"github.com/amd/gaia/tui/internal/setupapi"
)

// Every sentence the setup screens show, in one file, so first-run setup,
// /models and /gpu say the same thing in the same words, and so a wording fix
// is one edit. Plain words, the outcome first, numbers with units.
//
// Where the Agent UI shows the same state it uses the same sentence; the only
// differences are the TUI's keys ("press r") where the web has buttons.

const (
	titleModels     = "Choose your models"
	titleSettings   = "Models on this PC"
	titleGPU        = "Give the GPU more memory"
	titleGuide      = "Change the GPU memory yourself"
	titleDownload   = "Downloading models"
	titleContents   = "Everything being set up"
	titleTest       = "Testing everything"
	titleReady      = "GAIA is ready."
	titleLoading    = "Reading the models"
	titleNoServer   = "The local model server isn't running"
	introModels     = "These are the models GAIA will use on this PC, by job. Change any you like; you can add more later in Settings."
	introSettings   = "Tick a model to download it, untick it to stop using it. Unticking never deletes a model."
	cloudNoDownload = "Cloud models need no download."

	readingModels   = "reading models…"
	noServerBody    = "GAIA can't read which models it has until the server answers. Press r to try again once it is running, or esc to go back to setup's first step."
	noServerInstall = "GAIA can't read which models it has until the local model server runs. Press enter to install and start it (about 1 minute; no models download), then choose your models."
	tooSmall        = "Window too small. Enlarge to continue."
	zeroChat        = "Pick at least one chat model, or choose a cloud provider in step 1."
	noDetails       = "(nothing more to show)"
	keepTryingLabel = "Keep trying if the connection drops (up to 2 minutes)"

	statusOnPC      = "on PC"
	statusUnknown   = "size unknown"
	markRecommended = "* recommended"
	markNPU         = "NPU"
	fitUnknownChip  = "fit unknown"
	fitUnknownLine  = "GAIA couldn't read this PC's memory, so it can't tell if this fits."
	mayBeSlow       = "It may be very slow or fail to load."

	installedGroup = "%d more already in Lemonade (press l)"
	installedHide  = "Already in Lemonade (press l to hide)"

	estimating     = "Estimating…"
	stalledShort   = "No data for 15 seconds. Still trying…"
	stalledLong    = "The download seems stuck. Press r to retry."
	leavingCancels = "Leaving this screen cancels the download. Finished models stay."
	leavingKeeps   = "Downloads keep going if you switch away; the status line shows them."
	noPerModel     = "Per-model progress isn't available from this server version."
	cancelAsk      = "Stop downloading? Finished models stay. Partial downloads are kept so you can resume."
	runOwnsRetry   = "Setup is still downloading its own models. Retry this one once it finishes."
	resumeBanner   = "Last time's download didn't finish. Resume?"
	runVanished    = "The download stopped when GAIA's background service restarted. Finished models stay; press R to resume."
	startingLine   = "starting the download…"
	stoppingLine   = "stopping…"
	everythingHere = "Everything is already on this PC."
	lostContact    = "Lost contact with GAIA's background service. Press r to reconnect, or d for what went wrong."
	verifyingLine  = "checking that it loads…"
	verifyWaiting  = "downloaded · checked when the rest finish"
	setupCancelled = "Download stopped. Finished models stay; press r to resume."
	// The Agent UI's setup.progress.unchecked and setup.download.stoppedBeforeCheck,
	// with its "Finish setup" button as the TUI's R.
	uncheckedLine      = "Downloaded and kept. GAIA checks that it loads when you press R."
	stoppedBeforeCheck = "Setup stopped before it could check that the models load. They are kept; press R to check them now."
	setupStoppedNote   = "Setup stopped before it finished. Press r to try again, or d for what went wrong."
	checkingModels     = "Everything is downloaded. Checking that the models load…"

	// The Agent UI's setup.label, setup.job and setup.get.
	labelVerified    = "Verified"
	labelUntested    = "Untested"
	jobChatTitle     = "Chat"
	jobVisionTitle   = "Reading images"
	jobDocsTitle     = "Documents and memory"
	jobExtrasTitle   = "Optional extras"
	extrasOff        = "off by default"
	extrasNote       = "Off by default. GAIA asks before downloading these the first time you need them."
	untestedNote     = "Untested models are used at your own risk. They may run, but tools, documents or images might not work well."
	untestedPicked   = "GAIA will use this model, but we haven't tested it. You can switch in Settings at any time."
	nothingMore      = "Nothing more to download"
	otherVision      = "Choose a different vision model"
	hideVision       = "Hide other vision models"
	useChatForVision = "Use the chat model"
	skipVision       = "Skip for now"
	skipVisionNote   = "Reading images needs a vision model. GAIA will ask before downloading one."
	notWithThisChat  = "Not available with this chat model"

	// The Agent UI's setup.contents, with the size kept in a sentence that fits a row.
	contentsLede      = "Here's everything coming to this PC."
	contentsSizeLater = "Size shown when it starts"
	contentsWorking   = "In progress"
	contentsReady     = "Ready"
	contentsFailed    = "Failed"
	contentsHere      = "Already here"
	contentsWaiting   = "Waiting"
	contentsFrom      = "From "

	// The Agent UI's setup.test.
	testLede       = "A download isn't proof it works, so GAIA runs each part for real."
	testRunning    = "Running"
	testWaiting    = "Waiting"
	testPassed     = "Passed"
	testFailed     = "Failed"
	testSkipped    = "Not on this PC"
	testFailedHelp = "Something didn't work, so GAIA isn't ready yet. Your downloads are safe."
	testFailedMem  = "This PC doesn't have enough free memory right now. Close memory-heavy apps and try again, or choose a smaller model."

	gpuKeepsFmt    = "GAIA keeps at least %s for %s and your apps."
	gpuConsentKey  = "Changing it needs administrator permission and a restart."
	gpuWaitingText = "Waiting for permission…"
	gpuDeniedText  = "Permission wasn't given, so nothing changed."
	gpuGuidedNote  = "GAIA can't change this setting itself on this PC, so it shows you where to change it."
	gpuIveDone     = "I've done this"
	gpuNoChange    = "A bigger GPU share doesn't change which models fit this PC, so there is nothing to change here."
	// gpuNothingToGive replaces the guided steps when the PC has no memory to
	// move to the GPU: steps for 0 GB would starve it.
	gpuNothingToGive = "This PC can't give the GPU more memory and still keep enough for the system."
	gpuRestartHow    = "GAIA never restarts it for you; save your work first."
)

// visionBuiltIn says the chat model reads images itself.
func visionBuiltIn(name string) string { return "Handled by " + name + " · " + nothingMore }

// visionNeeded says why a second model is ticked.
func visionNeeded(name string) string {
	return name + " can't read images, so we picked a model for that job."
}

// imagesBlockedLine says why image creation can't be ticked.
func imagesBlockedLine(name string) string {
	return name + " doesn't have this feature yet. Choose a larger chat model to use it."
}

// extraLabel is an optional extra by the job it does.
func extraLabel(role string) string {
	switch role {
	case setupapi.RoleSpeech:
		return "Speech to text"
	case setupapi.RoleVoice:
		return "Text to speech"
	case setupapi.RoleImageCreation:
		return "Creating images"
	}
	return role
}

func extraNote(role string) string {
	switch role {
	case setupapi.RoleSpeech:
		return "For talking to GAIA and transcribing audio."
	case setupapi.RoleVoice:
		return "So GAIA can read answers aloud."
	case setupapi.RoleImageCreation:
		return "Makes pictures from a description."
	}
	return ""
}

// componentName is a setup component in the Agent UI's words; an id the TUI
// doesn't know yet still shows, under its own name.
func componentName(id string) string {
	switch id {
	case "server":
		return "GAIA's model server"
	case "backend":
		return "Model runtime"
	case "packages":
		return "Python packages"
	case "agent":
		return "GAIA agent"
	}
	return id
}

// testJobLabel says what a test proves.
func testJobLabel(job string) string {
	switch job {
	case setupapi.JobServer:
		return "Model server answers"
	case setupapi.RoleChat:
		return "Chat: loads and answers"
	case setupapi.RoleVision:
		return "Reading images: names a test image's colour"
	case setupapi.RoleEmbedding:
		return "Documents and memory: makes an embedding"
	case setupapi.RoleSpeech:
		return "Speech to text: transcribes a test sound"
	case setupapi.RoleVoice:
		return "Text to speech: speaks a sentence"
	case setupapi.RoleImageCreation:
		return "Creating images: makes a test picture"
	case setupapi.JobAgent:
		return "A real chat with GAIA"
	}
	return job
}

// testResult is a row's outcome.
func testResult(r setupapi.TestRow) string {
	switch r.State {
	case setupapi.TestRunning:
		return testRunning
	case setupapi.TestFailed:
		return testFailed
	case setupapi.TestSkipped:
		return testSkipped
	}
	if r.Seconds != nil {
		return fmt.Sprintf("%s, %.0f s", testPassed, *r.Seconds)
	}
	return testPassed
}

func testCount(done, total int) string {
	return fmt.Sprintf("Tests done: %d of %d", done, total)
}

func downloadAgain(name string) string { return "Press x to download " + name + " again." }

// extraFailed is an optional extra that failed its test: GAIA is ready without it.
func extraFailed(label, why string) string {
	return strings.TrimSuffix(label, ".") + " didn't pass its test: " + why
}

// alsoSetUp is the models screen's line for everything that isn't a model.
func alsoSetUp(names []string) string {
	return "Also downloads " + strings.Join(names, ", ") + ". Press v to see everything."
}

func introFor(settings bool) string {
	if settings {
		return introSettings
	}
	return introModels
}

// statusDownload is a row that still needs downloading.
func statusDownload(gb float64) string { return formatGB(gb) }

// statusPartial is a row with some bytes already on disk.
func statusPartial(left float64) string { return formatGB(left) + " left" }

// A model's need and the PC's memory for models both come from model_fit in
// the same unit, so they carry the same label and compare directly. Whether a
// model fits is still the backend's verdict, never re-derived here.

// Sentences below match the Agent UI's setup copy (en.ts setup.* and gpu.*),
// with the TUI's keys where the web has buttons.

// fitLine is the focused row's memory line when the model fits.
func fitLine(need, have float64) string {
	return fmt.Sprintf("Needs about %s of memory; this PC has %s for models.", formatGBShort(need), formatGBShort(have))
}

// noFitLine is the warning under a model that won't fit this PC's memory.
func noFitLine(need, have float64) string {
	return fmt.Sprintf("[!] Needs about %s; this PC has %s. %s", formatGBShort(need), formatGBShort(have), mayBeSlow)
}

// noFitAsk replaces the footer before downloading a model that won't fit.
func noFitAsk(need, have float64) string {
	return fmt.Sprintf("Needs about %s; this PC has %s.", formatGBShort(need), formatGBShort(have))
}

// purposeLine says what a model is for in the job it is listed under.
func purposeLine(e rowEntry, job string) string {
	switch {
	case e.optional != "":
		return extraNote(e.optional)
	case e.embedding:
		return "Lets GAIA search your files and remember things."
	case job == jobVision:
		return "Reads images and screenshots."
	case e.npu:
		return "Answers your chats. Runs on the NPU."
	case e.readsImages:
		return "Answers your chats and reads images."
	}
	return "Answers your chats."
}

// skippedCaveat is the amber line under a model the user unticked that some
// feature needs.
func skippedCaveat(e rowEntry) string {
	switch {
	case e.vision:
		return "[!] Without " + e.name + ", GAIA can't read images or screenshots. You can add it later in Settings."
	case e.embedding:
		return "[!] Without " + e.name + ", GAIA can't search your files or remember things. You can add it later in Settings."
	}
	return ""
}

// diskSummary is the live totals line.
func diskSummary(download float64, free *float64, drive string) string {
	if free == nil {
		return fmt.Sprintf("Download %s · Free space unknown", formatGB(download))
	}
	after := *free - download
	if after < 0 {
		return fmt.Sprintf("Download %s · free on %s %s · %s short",
			formatGB(download), drive, formatGB(*free), formatGB(-after))
	}
	return fmt.Sprintf("Download %s · free on %s %s · after %s",
		formatGB(download), drive, formatGB(*free), formatGB(after))
}

// diskShortfall names how much to free, and the one model to drop when
// dropping it alone is enough.
func diskShortfall(need, free float64, drive, drop string, dropGB float64) string {
	short := need - free
	if drop != "" {
		return fmt.Sprintf("Needs %s; only %s free on %s. Free %s or untick %s (saves %s).",
			formatGB(need), formatGB(free), drive, formatGB(short), drop, formatGB(dropGB))
	}
	return fmt.Sprintf("Needs %s; only %s free on %s. Free %s, or untick more models.",
		formatGB(need), formatGB(free), drive, formatGB(short))
}

// failureLine is a failed row's reason, worded for its kind.
func failureLine(kind, name, drive string, doneGB, leftGB float64) string {
	switch kind {
	case "disk_full":
		return fmt.Sprintf("Ran out of disk space on %s after %s of %s. Free %s and press r. Nothing was lost; the download continues from where it stopped.",
			drive, formatGB(doneGB), name, formatGB(leftGB))
	case "network":
		return "The connection dropped while downloading " + name + ". Press r to continue from where it stopped."
	case "verify":
		return name + " downloaded but didn't load. Press r to delete it and download it again."
	}
	return "Lemonade couldn't download " + name + ". Press d for its message, r to retry."
}

// retryingLine is a row waiting out a dropped connection the user opted into.
func retryingLine(secondsLeft int) string {
	return fmt.Sprintf("The connection dropped. Trying again for %s…", plural(secondsLeft, "more second"))
}

// redownloadAsk is the confirm before retrying a model that failed its load
// check: the retry deletes it first.
func redownloadAsk(name string) string {
	return name + " downloaded but didn't load. Delete it and download it again?"
}

// removeAsk is the confirm before deleting a model from this PC.
func removeAsk(e rowEntry) string {
	lost := ""
	switch {
	case e.vision:
		lost = " and turns off vision"
	case e.embedding:
		lost = " and turns off document search and memory"
	}
	frees := ""
	if e.fullGB > 0 {
		frees = " This frees " + formatGB(e.fullGB) + lost + "."
	}
	return "Remove " + e.name + "?" + frees + " You can download it again."
}

// GPU copy.

func gpuBody(total, gpu float64) string {
	return fmt.Sprintf("This PC shares %s between the CPU and the GPU. The GPU has %s now. Models run on the GPU, so more here means bigger models.",
		formatGBShort(total), formatGBShort(gpu))
}

func gpuSplit(cpu, gpu float64) string {
	return fmt.Sprintf("CPU %s  |  GPU %s", formatGBShort(cpu), formatGBShort(gpu))
}

func gpuUnlocks(target float64, models []string) string {
	switch len(models) {
	case 0:
		return ""
	case 1:
		return fmt.Sprintf("With %s for the GPU, %s fits.", formatGBShort(target), models[0])
	}
	return fmt.Sprintf("With %s for the GPU, %s and %s fit.", formatGBShort(target), models[0], models[1])
}

func gpuConsent(name string, pool, now float64) string {
	return fmt.Sprintf("GAIA will change %s so the GPU has %s. This needs administrator permission and a restart. Nothing else changes, and you can change it back with /gpu. The GPU has %s now. GAIA saves this setting so you can undo.",
		name, formatGBShort(pool), formatGBShort(now))
}

// gpuDone and gpuPendingLine take the GPU memory as words: "40 GB", or "the
// new GPU memory" when no preset says what a setting gives.
func gpuDone(pool string) string {
	return fmt.Sprintf("Done. Restart your PC to use %s. Downloads can start now; restart when they finish.", pool)
}

func gpuPendingLine(pool string) string {
	return fmt.Sprintf("Restart your PC to use %s. %s", pool, gpuRestartHow)
}

func gpuTookEffect(now float64) string {
	return fmt.Sprintf("GPU memory is now %s.", formatGBShort(now))
}

func gpuNotApplied(now float64) string {
	return fmt.Sprintf("The PC still reports %s. The change may not have applied. Try again, or follow the steps to set it yourself.", formatGBShort(now))
}

// Ready summary.

func readyChat(name, where string) string { return "Chat       " + name + "   (" + where + ")" }
func readyVision(name string) string      { return "Vision     " + name }
func readyDocs(name string) string        { return "Documents  " + name }
func readyVisionOff(name string) string {
	return "Vision: off. Add " + name + " in Settings (/models) to read images."
}
func readyDocsOff(name string) string {
	return "Documents and memory: off. Add " + name + " in Settings (/models) to search your files and remember things."
}
func readyNotInstalled(names []string) string {
	return "Not installed (add later in Settings, /models): " + strings.Join(names, ", ")
}
func readyGPU(now, was float64) string {
	return fmt.Sprintf("GPU memory is now %s (was %s). Undo with /gpu.", formatGBShort(now), formatGBShort(was))
}

func plural(n int, word string) string {
	if n == 1 {
		return fmt.Sprintf("%d %s", n, word)
	}
	return fmt.Sprintf("%d %ss", n, word)
}
