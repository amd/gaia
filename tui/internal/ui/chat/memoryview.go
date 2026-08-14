package chat

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/theme"
)

// memoryFetchTimeout bounds how long /memory waits on the agent. The fetch is
// a local SQLite read through a store the agent already has open — normally
// well under a second — so a timeout this long only ever fires when the
// agent process itself is wedged, and Esc cancels sooner if the user does
// not want to wait even that long.
const memoryFetchTimeout = 20 * time.Second

// memoryContentTruncateAt caps how much of one memory row's content is shown
// inline before the "+N chars" marker takes over. Long enough that the
// overwhelming majority of real entries (short facts and preferences) show
// in full; short enough that one pathological entry near MemoryStore's
// 2000-char cap cannot eat a whole card's row budget by itself.
const memoryContentTruncateAt = 320

// memoryDumpMsg carries the result of a /memory fetch back into Update().
type memoryDumpMsg struct {
	dump client.MemoryDump
	err  error
}

// startMemoryFetch kicks off a /memory snapshot fetch. It deliberately does
// not touch m.streaming: a memory dump reads the agent's already-open store
// directly and never runs the LLM loop, so it must not look like (or block
// behind) a chat turn — see ChatModel.submit's queueing of slash commands
// behind a live turn, which already keeps this from overlapping one.
func (m ChatModel) startMemoryFetch() (tea.Model, tea.Cmd) {
	provider, ok := m.client.(client.MemoryProvider)
	if !ok {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: "This agent does not support /memory.",
		})
		m.updateViewport()
		return m, nil
	}

	m.memoryLoading = true
	m.memoryView = nil
	m.updateViewport()

	ctx, cancel := context.WithTimeout(context.Background(), memoryFetchTimeout)
	m.memoryCancelFn = cancel

	return m, tea.Batch(m.spinner.Tick, func() tea.Msg {
		dump, err := provider.FetchMemory(ctx)
		return memoryDumpMsg{dump: dump, err: err}
	})
}

// dismissMemoryView clears whatever /memory left on screen — the finished
// view, or a fetch still in flight, which this also cancels.
func (m ChatModel) dismissMemoryView() tea.Model {
	if m.memoryCancelFn != nil {
		m.memoryCancelFn()
		m.memoryCancelFn = nil
	}
	m.memoryLoading = false
	m.memoryView = nil
	m.updateViewport()
	return m
}

// handleMemoryDump lands a /memory fetch's result. A failure (including a
// cancelled/timed-out fetch) is reported as a status line, not a blank or
// silently-dropped view — an agent that never answered is a different fact
// from "you have no memories" (CLAUDE.md: no silent fallbacks).
func (m ChatModel) handleMemoryDump(msg memoryDumpMsg) (tea.Model, tea.Cmd) {
	m.memoryLoading = false
	m.memoryCancelFn = nil

	if msg.err != nil {
		m.messages = append(m.messages, Message{
			Role:    RoleStatus,
			Content: fmt.Sprintf("[!] could not load memory: %v", msg.err),
		})
		m.updateViewport()
		return m, nil
	}

	dump := msg.dump
	m.memoryView = &dump
	m.updateViewport()
	return m, nil
}

// ---------------------------------------------------------------------------
// Rendering
//
// Sections are delineated by a filled band, not a border. Boxing every category
// spent four columns and two rows per group on chrome, and stacked five frames
// down the pane so the eye landed on the frames instead of the entries. The band
// is SurfaceBG/OnSurface — the one surface pair the contrast suite already holds
// to ≥1.5:1 against every terminal background AND 4.5:1 for its own text, so a
// section header cannot vanish on a Nord or Solarized Light terminal.
//
// Within a section each entry gets three ranks of emphasis, which is what makes
// a wall of similar-looking rows scannable:
//
//	subject   Info      the tool or key the entry is about
//	body      Text      what was learned
//	metadata  Faint     confidence and age, on their OWN line
//
// Metadata used to trail the prose ("…and should be ignored rat… [conf 0.50 ·
// updated 2026-08-14 15:00]"), which put the least important text of the entry
// in the position the eye reads last and mistakes for the end of the sentence.
// ---------------------------------------------------------------------------

// memoryMeasure caps the wrap width. Prose set to the full width of a wide
// terminal is one long scan line per entry — the eye loses its place returning
// to the left margin. Everything here is prose, so it gets a measure.
const memoryMeasure = 76

// memoryIndent is the gutter every entry hangs in, so the bands are the only
// thing on the left margin and the sections read as sections.
const memoryIndent = "  "

// memoryPathElide is the length past which an absolute path is shown as its
// last few segments. The full path of a temp-dir fixture is 90 columns of which
// the last 30 identify it; the rest pushes the actual content off the line.
const memoryPathElide = 44

var (
	// The band. Bold-on-fill, full pane width, so a section boundary is legible
	// as a shape before any text is read.
	memoryBandStyle = lipgloss.NewStyle().
			Background(theme.SurfaceBG).
			Foreground(theme.OnSurface).
			Bold(true)

	memorySubjectStyle = lipgloss.NewStyle().Foreground(theme.Info)
	memoryBodyStyle    = lipgloss.NewStyle().Foreground(theme.Text)
	memoryMetaStyle    = lipgloss.NewStyle().Foreground(theme.Faint)
	memorySummaryStyle = lipgloss.NewStyle().Foreground(theme.Dim)
	// Sensitive is called out in a word, not by colour alone (R2).
	memoryFlagStyle = lipgloss.NewStyle().Foreground(theme.Warning)
)

// renderMemoryView renders one full /memory snapshot at the given outer width.
func renderMemoryView(dump client.MemoryDump, width int) string {
	if !dump.Available {
		reason := dump.Reason
		if reason == "" {
			reason = "Memory is unavailable for this session."
		}
		body := lipgloss.NewStyle().Bold(true).Render("Memory unavailable") +
			"\n\n" + reason
		return errorPanelStyle.Width(width).Render(body)
	}

	var lines []string
	lines = append(lines, renderMemorySummary(dump, width)...)

	for _, group := range groupMemoryItemsByCategory(dump.Items) {
		lines = append(lines, "")
		lines = append(lines, renderMemorySection(group, dump.Stats.ByCategory[group.category], width)...)
	}

	if len(dump.Items) == 0 {
		lines = append(lines, "", memorySummaryStyle.Render(memoryGutter(width)+
			truncateToWidth("No memories stored yet.", memoryWrapWidth(width))))
	}

	return strings.Join(lines, "\n")
}

// memoryBand draws one full-width section header: a label on the left, a count
// flush right, filled edge to edge. The fill is what separates sections, so it
// must span the pane exactly — short and it reads as a ragged highlight.
func memoryBand(label, count string, width int) string {
	if width < 8 {
		return memoryBandStyle.Render(truncateToWidth(label, width))
	}
	label = " " + strings.ToUpper(label)
	count = count + " "
	gap := width - lipgloss.Width(label) - lipgloss.Width(count)
	if gap < 1 {
		return memoryBandStyle.Render(padToWidth(truncateToWidth(label, width), width))
	}
	return memoryBandStyle.Render(label + strings.Repeat(" ", gap) + count)
}

func renderMemorySummary(dump client.MemoryDump, width int) []string {
	lines := []string{
		memoryBand("memory", fmt.Sprintf("%d of %d entries", dump.Shown, dump.Total), width),
	}

	detail := []string{
		formatMemoryCounts(dump.Stats.ByCategory),
		formatMemoryContextCounts(dump.Contexts),
		fmt.Sprintf("%d entities · avg confidence %.2f", dump.Stats.EntityCount, dump.Stats.AvgConfidence),
	}
	for _, d := range detail {
		if d == "" || d == "—" {
			continue
		}
		for _, line := range wrapMemoryText(d, memoryWrapWidth(width)) {
			lines = append(lines, memorySummaryStyle.Render(memoryGutter(width)+line))
		}
	}

	if dump.Stats.SensitiveCount > 0 {
		// Named, not hidden: this view exists because a plaintext secret was
		// found sitting in memory with no way to see it was there.
		note := fmt.Sprintf("%d sensitive — shown below, not redacted", dump.Stats.SensitiveCount)
		lines = append(lines, memoryFlagStyle.Render(memoryGutter(width)+truncateToWidth(note, memoryWrapWidth(width))))
	}
	return lines
}

func renderMemorySection(group memoryCategoryGroup, totalForCategory, width int) []string {
	shown := len(group.items)
	count := fmt.Sprintf("%d", shown)
	if totalForCategory > shown {
		count = fmt.Sprintf("%d of %d", shown, totalForCategory)
	}

	lines := []string{memoryBand(group.category, count, width)}
	for i, item := range group.items {
		if i > 0 {
			lines = append(lines, "")
		}
		lines = append(lines, renderMemoryEntry(item, width)...)
	}
	return lines
}

// renderMemoryEntry draws one entry across as many lines as it needs: the
// subject picked out from the body, then the body, then a metadata line.
func renderMemoryEntry(item client.MemoryItem, width int) []string {
	wrapAt := memoryWrapWidth(width)
	subject, body := splitMemorySubject(elideMemoryPaths(item.Content))

	if len(body) > memoryContentTruncateAt {
		cut := body[:memoryContentTruncateAt]
		body = fmt.Sprintf("%s… (+%d more)", cut, len(body)-memoryContentTruncateAt)
	}

	var lines []string
	first := memoryGutter(width)
	if subject != "" {
		first += memorySubjectStyle.Render(subject) + memoryBodyStyle.Render("  ")
	}
	// The subject shares the first line with the body, so that line wraps at a
	// narrower measure than the ones under it.
	head := wrapMemoryText(body, wrapAt-lipgloss.Width(subject)-2)
	if len(head) == 0 {
		head = []string{""}
	}
	lines = append(lines, first+memoryBodyStyle.Render(head[0]))
	for _, rest := range wrapMemoryText(strings.Join(head[1:], " "), wrapAt) {
		if rest == "" {
			continue
		}
		lines = append(lines, memoryGutter(width)+memoryBodyStyle.Render(rest))
	}

	if meta := formatMemoryMeta(item); meta != "" {
		lines = append(lines, memoryMetaStyle.Render(memoryGutter(width)+truncateToWidth(meta, wrapAt)))
	}
	return lines
}

// splitMemorySubject peels a leading identifier off an entry — "edit_file:
// 'SSEOutputHandler' object has no attribute" is really a subject and a body,
// and colouring the two differently is what lets a column of errors be scanned
// by the tool they came from. Only splits on something that actually looks like
// an identifier, so an ordinary sentence containing a colon is left alone.
func splitMemorySubject(content string) (subject, body string) {
	idx := strings.Index(content, ": ")
	if idx <= 0 || idx > 40 {
		return "", content
	}
	head := content[:idx]
	if strings.ContainsAny(head, " \t'\"") {
		return "", content
	}
	return head, strings.TrimSpace(content[idx+1:])
}

// elideMemoryPaths shortens long absolute paths to their last few segments.
// A temp-dir fixture path runs 90 columns of which the tail 30 identify it; at
// full length it pushes the thing actually learned off the visible line.
func elideMemoryPaths(content string) string {
	fields := strings.Fields(content)
	changed := false
	for i, f := range fields {
		if len(f) <= memoryPathElide {
			continue
		}
		sep := ""
		switch {
		case strings.Count(f, `\`) >= 3:
			sep = `\`
		case strings.Count(f, "/") >= 3:
			sep = "/"
		default:
			continue
		}
		// Stored content often carries the path as it was escaped in a Python
		// repr, so the separators are DOUBLED. Splitting on a single one then
		// yields empty segments and the tail renders as "…\.bin\\autoprefixer".
		parts := make([]string, 0, 8)
		for _, p := range strings.Split(f, sep) {
			if p != "" {
				parts = append(parts, p)
			}
		}
		if len(parts) < 3 {
			continue
		}
		fields[i] = "…" + sep + strings.Join(parts[len(parts)-3:], sep)
		changed = true
	}
	if !changed {
		return content
	}
	return strings.Join(fields, " ")
}

func formatMemoryMeta(item client.MemoryItem) string {
	var meta []string
	if item.Entity != "" {
		meta = append(meta, item.Entity)
	}
	if item.Context != "" && item.Context != "global" {
		meta = append(meta, item.Context)
	}
	meta = append(meta, fmt.Sprintf("confidence %.2f", item.Confidence))
	if u := formatMemoryTimestamp(item.UpdatedAt); u != "" {
		meta = append(meta, u)
	}
	if item.Sensitive {
		meta = append(meta, "sensitive")
	}
	return strings.Join(meta, " · ")
}

// memoryGutter is the indent entries hang in — dropped entirely on a pane too
// narrow to spare it, where two columns of margin is two columns of content.
func memoryGutter(width int) string {
	if width < 12 {
		return ""
	}
	return memoryIndent
}

// memoryWrapWidth is the measure prose is set to: the pane, less the gutter,
// capped so a wide terminal does not produce one long scan line per entry. It
// can never exceed what is left of the pane — a floor here instead of a clamp
// is what let a 3-column pane emit 10-column lines.
func memoryWrapWidth(width int) int {
	w := width - len(memoryGutter(width))
	if w > memoryMeasure {
		w = memoryMeasure
	}
	if w < 1 {
		w = 1
	}
	return w
}

func wrapMemoryText(s string, w int) []string {
	if w < 1 {
		w = 1
	}
	fields := strings.Fields(s)
	if len(fields) == 0 {
		return nil
	}
	var out []string
	cur := ""
	for _, f := range fields {
		f = truncateToWidth(f, w) // a single word wider than the measure
		switch {
		case cur == "":
			cur = f
		case lipgloss.Width(cur)+1+lipgloss.Width(f) <= w:
			cur += " " + f
		default:
			out = append(out, cur)
			cur = f
		}
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}

func truncateToWidth(s string, w int) string {
	if w <= 0 {
		return ""
	}
	if lipgloss.Width(s) <= w {
		return s
	}
	r := []rune(s)
	for len(r) > 0 && lipgloss.Width(string(r))+1 > w {
		r = r[:len(r)-1]
	}
	return string(r) + "…"
}

func padToWidth(s string, w int) string {
	if n := w - lipgloss.Width(s); n > 0 {
		return s + strings.Repeat(" ", n)
	}
	return s
}

type memoryCategoryGroup struct {
	category string
	items    []client.MemoryItem
}

// groupMemoryItemsByCategory groups the page of items the agent returned,
// sorted alphabetically by category so repeat views land in the same order.
func groupMemoryItemsByCategory(items []client.MemoryItem) []memoryCategoryGroup {
	byCat := map[string][]client.MemoryItem{}
	for _, item := range items {
		byCat[item.Category] = append(byCat[item.Category], item)
	}
	cats := make([]string, 0, len(byCat))
	for c := range byCat {
		cats = append(cats, c)
	}
	sort.Strings(cats)

	groups := make([]memoryCategoryGroup, 0, len(cats))
	for _, c := range cats {
		groups = append(groups, memoryCategoryGroup{category: c, items: byCat[c]})
	}
	return groups
}

func formatMemoryCounts(counts map[string]int) string {
	if len(counts) == 0 {
		return "—"
	}
	keys := make([]string, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	// Biggest first — "what is mostly in here" is the question this line
	// answers. Ties break alphabetically so repeat views do not reshuffle.
	sort.Slice(keys, func(i, j int) bool {
		if counts[keys[i]] != counts[keys[j]] {
			return counts[keys[i]] > counts[keys[j]]
		}
		return keys[i] < keys[j]
	})
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s %d", k, counts[k]))
	}
	return strings.Join(parts, " · ")
}

func formatMemoryContextCounts(contexts []client.MemoryContext) string {
	if len(contexts) == 0 {
		return "—"
	}
	parts := make([]string, 0, len(contexts))
	for _, c := range contexts {
		parts = append(parts, fmt.Sprintf("%s %d", c.Context, c.Count))
	}
	return strings.Join(parts, " · ")
}

// formatMemoryTimestamp trims an ISO 8601 timestamp
// ("2026-08-13T14:22:05.123456-07:00") to "2026-08-13 14:22" — enough to
// judge how stale an entry is without the noise of seconds/timezone offset.
func formatMemoryTimestamp(ts string) string {
	if len(ts) < 16 {
		return ts
	}
	return strings.Replace(ts[:16], "T", " ", 1)
}
