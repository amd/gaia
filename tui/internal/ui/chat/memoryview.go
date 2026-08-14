package chat

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/amd/gaia/tui/internal/client"
	"github.com/amd/gaia/tui/internal/ui/cards"
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
// Rendering — grouped by category, one "list" card per category, reusing the
// same primitives a tool_result card draws with (box, wrap, per-card
// truncation-with-count). A wall of unwrapped rows was the exact failure mode
// this view exists to avoid.
// ---------------------------------------------------------------------------

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

	var sb strings.Builder
	sb.WriteString(renderMemorySummaryCard(dump, width))

	groups := groupMemoryItemsByCategory(dump.Items)
	for _, group := range groups {
		sb.WriteString("\n\n")
		sb.WriteString(renderMemoryCategoryCard(group, dump.Stats.ByCategory[group.category], width))
	}

	if len(dump.Items) == 0 {
		sb.WriteString("\n\n")
		sb.WriteString(activityStyle.Render("  No memories stored yet."))
	}

	return sb.String()
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

// memoryKVItem/memoryKVPayload mirror the `key_value` card contract
// (docs/spec/agent-ui-query-sse-contract.md §4.3) — the same shape a real
// tool_result would send, so cards.Render draws it with no special-casing.
type memoryKVItem struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}
type memoryKVPayload struct {
	Title string         `json:"title"`
	Items []memoryKVItem `json:"items"`
}

func renderMemorySummaryCard(dump client.MemoryDump, width int) string {
	title := fmt.Sprintf("Memory — %d of %d entries", dump.Shown, dump.Total)

	items := []memoryKVItem{
		{Key: "Categories", Value: formatMemoryCounts(dump.Stats.ByCategory)},
		{Key: "Contexts", Value: formatMemoryContextCounts(dump.Contexts)},
		{Key: "Entities", Value: fmt.Sprintf("%d", dump.Stats.EntityCount)},
		{Key: "Avg confidence", Value: fmt.Sprintf("%.2f", dump.Stats.AvgConfidence)},
	}
	if dump.Stats.SensitiveCount > 0 {
		// Named, not hidden: this view exists because a plaintext secret was
		// found sitting in memory with no way to see it was there.
		items = append(items, memoryKVItem{
			Key:   "Sensitive",
			Value: fmt.Sprintf("%d — shown below, not redacted", dump.Stats.SensitiveCount),
		})
	}

	payload, _ := json.Marshal(memoryKVPayload{Title: title, Items: items})
	return cards.Render("key_value", payload, width)
}

func formatMemoryCounts(counts map[string]int) string {
	if len(counts) == 0 {
		return "—"
	}
	keys := make([]string, 0, len(counts))
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s: %d", k, counts[k]))
	}
	return strings.Join(parts, ", ")
}

func formatMemoryContextCounts(contexts []client.MemoryContext) string {
	if len(contexts) == 0 {
		return "—"
	}
	parts := make([]string, 0, len(contexts))
	for _, c := range contexts {
		parts = append(parts, fmt.Sprintf("%s: %d", c.Context, c.Count))
	}
	return strings.Join(parts, ", ")
}

// memoryListPayload mirrors the `list` card contract — items wrap across
// multiple lines rather than being cut to one, which is what makes prose-y
// memory content (a "fact" or "note" can run to a full sentence or two)
// readable instead of shorn.
type memoryListPayload struct {
	Title   string   `json:"title"`
	Ordered bool     `json:"ordered"`
	Items   []string `json:"items"`
}

func renderMemoryCategoryCard(group memoryCategoryGroup, totalForCategory int, width int) string {
	shown := len(group.items)
	title := fmt.Sprintf("%s (%d)", group.category, shown)
	if totalForCategory > shown {
		title = fmt.Sprintf("%s — %d of %d", group.category, shown, totalForCategory)
	}

	items := make([]string, 0, len(group.items))
	for _, it := range group.items {
		items = append(items, formatMemoryItemLine(it))
	}

	payload, _ := json.Marshal(memoryListPayload{Title: title, Items: items})
	return cards.Render("list", payload, width)
}

// formatMemoryItemLine is one row: the (possibly truncated) content, then a
// bracketed metadata trailer with everything the task asked to see at a
// glance — entity, context, confidence, and when it was last touched.
func formatMemoryItemLine(item client.MemoryItem) string {
	content := item.Content
	if len(content) > memoryContentTruncateAt {
		cut := content[:memoryContentTruncateAt]
		content = fmt.Sprintf("%s… (+%d more chars)", cut, len(content)-memoryContentTruncateAt)
	}

	var meta []string
	if item.Entity != "" {
		meta = append(meta, item.Entity)
	}
	if item.Context != "" && item.Context != "global" {
		meta = append(meta, item.Context)
	}
	meta = append(meta, fmt.Sprintf("conf %.2f", item.Confidence))
	if u := formatMemoryTimestamp(item.UpdatedAt); u != "" {
		meta = append(meta, "updated "+u)
	}
	if item.Sensitive {
		meta = append(meta, "sensitive")
	}

	return content + "  [" + strings.Join(meta, " · ") + "]"
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
