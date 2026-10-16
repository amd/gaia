// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package chat

import (
	"encoding/json"
	"fmt"
	"net/url"
	"path/filepath"
	"strings"

	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/lemonade"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// Every turn that used tools ends with one quiet line saying where it ran and
// what went online, and the pages and documents it drew on are listed as
// links:
//
//	ran on this PC · read 2 pages from nps.gov · 1 document
//	Sources
//	  nps.gov/yose/planyourvisit/fees.htm
//	  park_guide.pdf
//
// GAIA's promise is that work stays on your PC; this is where the screen says
// whether it did.

// sourcesMax is how many sources are listed before the rest fold.
const sourcesMax = 5

var (
	provenanceWarnStyle = lipgloss.NewStyle().Foreground(theme.Warning)
	sourceStyle         = lipgloss.NewStyle().Foreground(theme.Text).Underline(true)
)

// provenance is where a finished turn ran and what it drew on.
type provenance struct {
	// place is "this PC" or the cloud provider the model ran on.
	place  string
	online []string
	// liveFailed is set when every web search and page fetch failed.
	liveFailed bool
	sources    []string
}

// turnPlace names where this session's model runs.
func (m ChatModel) turnPlace() string {
	switch {
	case !m.modelRemote:
		return "this PC"
	case m.modelBackend == "claude":
		return "Anthropic"
	case m.modelBackend != "" && m.modelBackend != "lemonade":
		return lemonade.Label(m.modelBackend)
	}
	return "a remote model"
}

// turnProvenance reads a finished turn's work. Nil when it used no tools:
// an answer from the model alone gets nothing added.
func turnProvenance(work []ActivityItem, place string) *provenance {
	var steps []ActivityItem
	for _, it := range work {
		if isStep(it) {
			steps = append(steps, it)
		}
	}
	if len(steps) == 0 {
		return nil
	}
	p := &provenance{place: place}
	live, liveOK := 0, 0
	pagesBy := map[string]int{}
	var hosts []string
	docs := map[string]bool{}
	seen := map[string]bool{}
	for _, it := range steps {
		ok := it.Done && !failedItem(it)
		switch it.Call.Category {
		case catWeb:
			live++
			if ok {
				liveOK++
			}
		case catFetch:
			live++
			if ok {
				liveOK++
			}
		}
		if !ok {
			continue
		}
		for _, src := range it.Sources {
			if seen[src] {
				continue
			}
			seen[src] = true
			p.sources = append(p.sources, src)
			if host := webHost(src); host != "" {
				if pagesBy[host] == 0 {
					hosts = append(hosts, host)
				}
				pagesBy[host]++
			} else {
				docs[src] = true
			}
		}
	}
	if len(hosts) > 0 {
		pages := 0
		for _, n := range pagesBy {
			pages += n
		}
		text := fmt.Sprintf("read %s from %s", countNoun(pages, "page"), hosts[0])
		if len(hosts) > 1 {
			text += fmt.Sprintf(" and %s", countNoun(len(hosts)-1, "more site"))
		}
		p.online = append(p.online, text)
	}
	if len(docs) > 0 {
		p.online = append(p.online, countNoun(len(docs), "document"))
	}
	p.liveFailed = live > 0 && liveOK == 0
	return p
}

// webHost is the site a source came from, "" for a local document.
func webHost(src string) string {
	u, err := url.Parse(src)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return ""
	}
	return strings.TrimPrefix(u.Hostname(), "www.")
}

// callSources is what a call says it will draw on: the page it fetches, the
// document it searches.
func callSources(tool string, args json.RawMessage) []string {
	obj := decodeObject(args)
	switch categoryOf(tool) {
	case catFetch:
		if tool == "download_file" {
			return nil
		}
		if u := rawString(obj, "url"); webHost(u) != "" {
			return []string{u}
		}
	case catDocs:
		if p := rawString(obj, "file_path", "path", "file"); p != "" {
			return []string{p}
		}
	}
	return nil
}

// resultSources is the documents a search answered from.
func resultSources(tool string, data json.RawMessage) []string {
	if categoryOf(tool) != catDocs {
		return nil
	}
	obj := decodeObject(data)
	var out []string
	for _, key := range []string{"source_files", "sources"} {
		list, _ := obj[key].([]interface{})
		for _, v := range list {
			if s := strings.TrimSpace(stringOf(v)); s != "" {
				out = append(out, s)
			}
		}
	}
	return out
}

// renderProvenance is the quiet line and the sources under an answer.
func (m ChatModel) renderProvenance(p *provenance) []string {
	if p == nil {
		return nil
	}
	width := max(m.width-4, 8)
	// Whole parts give way on a narrow screen, never half a word.
	parts := append([]string{"ran on " + p.place}, p.online...)
	for len(parts) > 1 && displayWidth(strings.Join(parts, " · ")) > width {
		parts = parts[:len(parts)-1]
	}
	rows := []string{"  " + activityStyle.Render(truncateRunes(strings.Join(parts, " · "), width))}
	if p.liveFailed {
		rows = append(rows, "  "+provenanceWarnStyle.Render(truncateRunes(
			"[!] Couldn't get live data: every web search and page fetch failed, so this answer is not from the web.", width)))
	}
	if len(p.sources) == 0 {
		return rows
	}
	rows = append(rows, "  "+deliverableLabelStyle.Render("Sources"))
	for i, src := range p.sources {
		if i == sourcesMax {
			rows = append(rows, "    "+foldLineStyle.Render(fmt.Sprintf("… %d more", len(p.sources)-i)))
			break
		}
		rows = append(rows, "    "+sourceLink(src, truncateRunes(sourceText(src), max(width-2, 4))))
	}
	return rows
}

// sourceText is how a source prints: a page as host and path, a document by
// its file name.
func sourceText(src string) string {
	if webHost(src) != "" {
		return shortURL(src)
	}
	return clean(filepath.Base(src))
}

// sourceLink makes a source clickable in terminals that support OSC 8.
func sourceLink(src, text string) string {
	styled := sourceStyle.Render(text)
	if webHost(src) != "" {
		return osc8Prefix + src + osc8Sep + styled + osc8Close
	}
	return fileLink(src, styled)
}
