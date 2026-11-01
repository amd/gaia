// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package modelsetup

import (
	"fmt"
	"strings"
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
	titleReady      = "GAIA is ready."
	titleLoading    = "Reading the models"
	titleNoServer   = "The local model server isn't running"
	introModels     = "These are the models GAIA will use on this PC. Untick any you don't want; you can add them later in Settings."
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
	stalledLong    = "Download seems stuck. Press r to retry."
	leavingCancels = "Leaving this screen cancels the download. Finished models stay."
	leavingKeeps   = "Downloads keep going if you leave; the status line shows how far they are."
	noPerModel     = "Per-model progress isn't available from this server version."
	cancelAsk      = "Stop downloading? Finished models stay. Partial downloads are kept so you can resume."
	runOwnsRetry   = "Setup is still downloading its own models. Retry this one once it finishes."
	resumeBanner   = "Last time's download didn't finish. Resume?"
	runVanished    = "The download stopped when GAIA's background service restarted. Finished models stay; press R to resume."
	startingLine   = "starting the download…"
	stoppingLine   = "stopping…"
	everythingHere = "Everything is already on this PC."
	lostContactFmt = "Lost contact with GAIA: %s. Press r to reconnect."
	verifyingLine  = "checking the file…"
	verifyWaiting  = "downloaded · checked when the rest finish"
	setupCancelled = "Download stopped. Finished models stay; press r to resume."

	gpuKeepsFmt    = "GAIA keeps at least %s for Windows and your apps."
	gpuConsentKey  = "Needs administrator permission and a restart."
	gpuWaitingText = "Waiting for permission…"
	gpuDeniedText  = "Permission wasn't given, so nothing changed."
	gpuGuidedNote  = "GAIA can't change this setting itself on this PC, so it shows you where to change it."
	gpuIveDone     = "I've done this"
	gpuNoChange    = "A bigger GPU share doesn't change which models fit this PC, so there is nothing to change here."
)

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

// The model's need is decimal GB; the PC's memory for models comes from
// Lemonade in GiB. They are labelled as what they are, never converted into
// each other, and whether a model fits is the backend's verdict, not a
// comparison of the two numbers here.

// fitLine is the focused row's memory line when the model fits.
func fitLine(need, haveGiB float64) string {
	return fmt.Sprintf("Needs about %s of memory; this PC has %s for models.", formatGBShort(need), formatGiB(haveGiB))
}

// noFitLine is the warning under a model that won't fit this PC's memory.
func noFitLine(need, haveGiB float64) string {
	return fmt.Sprintf("[!] needs ~%s; this PC has %s. %s", formatGBShort(need), formatGiB(haveGiB), mayBeSlow)
}

// noFitAsk replaces the footer before downloading a model that won't fit.
func noFitAsk(need, haveGiB float64) string {
	return fmt.Sprintf("Needs ~%s, this PC has %s.", formatGBShort(need), formatGiB(haveGiB))
}

// purposeLine says what a model is for, in a few words.
func purposeLine(e rowEntry) string {
	switch {
	case e.embedding:
		return "Lets GAIA search your files and remember things."
	case e.vision:
		return "Reads images. Skip it and GAIA can't look at pictures."
	case e.npu:
		return "Runs on the NPU."
	case e.recommended:
		return "Best answers for this PC."
	}
	return "A chat model."
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
		return fmt.Sprintf("Download %s · free space unknown", formatGB(download))
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
		return fmt.Sprintf("Ran out of disk space on %s after %s. Free %s and press r to retry. Nothing was lost; the download continues from where it stopped.",
			drive, formatGB(doneGB), formatGB(leftGB))
	case "network":
		return "The connection dropped. Press r to retry; the download continues from where it stopped."
	case "verify":
		return "The file didn't pass its check. Press r to download it again."
	}
	return "Lemonade couldn't download " + name + ". Press r to retry, or d for what it said."
}

// retryingLine is a row waiting out a dropped connection the user opted into.
func retryingLine(secondsLeft int) string {
	return fmt.Sprintf("The connection dropped. GAIA will retry on its own for %s.", plural(secondsLeft, "more second"))
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

func gpuConsent(pool float64, name string, setting, current float64) string {
	return fmt.Sprintf("GAIA will change the GPU memory to %s (%s %s). This needs administrator permission and a restart. Nothing else changes, and you can change it back with /gpu. Current setting: %s, saved for undo.",
		formatGBShort(pool), name, formatGBShort(setting), formatGBShort(current))
}

func gpuDone(target float64) string {
	return fmt.Sprintf("Done. Restart your PC to use %s. Downloads can start now; restart when they finish.", formatGBShort(target))
}

func gpuPendingLine(target float64) string {
	return fmt.Sprintf("Restart your PC to use %s. GAIA checks the new split the next time it starts.", formatGBShort(target))
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
	return fmt.Sprintf("GPU memory %s (was %s). Undo with /gpu.", formatGBShort(now), formatGBShort(was))
}

func plural(n int, word string) string {
	if n == 1 {
		return fmt.Sprintf("%d %s", n, word)
	}
	return fmt.Sprintf("%d %ss", n, word)
}
