// Package cards renders a `tool_result`'s typed `render` card into terminal
// text.
//
// The `/query` SSE contract (docs/spec/agent-ui-query-sse-contract.md §4.2/§4.3)
// lets a sidecar declare how its result should be drawn — `email_pre_scan` for
// the email agent's inbox triage, plus five generic primitives any agent can
// emit with no client work. §7 makes the receiving end's obligations explicit:
// an unknown key degrades to a visible generic card with a raw dump, and a
// payload that fails its schema says so. A card is never dropped and never
// blanks the message.
//
// Of the five primitives this package draws three. `image` is base64 raster and
// cannot be shown in a terminal, so it degrades to a caption line; `diff` has no
// producer today and rides the unsupported-card fallback until one exists.
package cards

import (
	"encoding/json"
	"strings"
)

// Render draws the card for a tool_result's `render` key at the given outer
// width. It always returns something visible: unknown keys and malformed
// payloads become explicit fallback cards rather than empty strings.
//
// width is the total width the card may occupy, borders included.
func Render(renderKey string, data json.RawMessage, width int) string {
	rendered, _ := RenderDeduped(renderKey, data, width, nil)
	return rendered
}

// RenderDeduped draws a card exactly like Render, except for the two email
// card types (email_pre_scan, email_attention): an item whose message_id is
// already in seen is skipped rather than shown a second time, and every
// message_id this card ends up showing is returned so a caller rendering
// more than one card in one turn can thread the accumulated set into the
// next call. Every other render key returns no ids and behaves exactly like
// Render. seen may be nil, equivalent to Render (nothing is deduped).
func RenderDeduped(renderKey string, data json.RawMessage, width int, seen map[string]bool) (rendered string, ids []string) {
	key := strings.TrimSpace(renderKey)
	if key == "" {
		return renderUnsupported("(none)", data, width), nil
	}
	switch key {
	case "email_pre_scan":
		return renderEmailPreScan(data, width, seen)
	case "email_attention":
		return RenderEmailAttention(data, width, seen)
	case "table":
		return renderTable(data, width), nil
	case "key_value":
		return renderKeyValue(data, width), nil
	case "list":
		return renderList(data, width), nil
	case "image":
		return renderImage(data, width), nil
	default:
		return renderUnsupported(key, data, width), nil
	}
}

// dedupByMessageID drops items whose id is already in seen (an item with an
// empty id is never treated as a duplicate) and returns the surviving items
// plus the non-empty ids they carry.
func dedupByMessageID[T any](items []T, id func(T) string, seen map[string]bool) (kept []T, ids []string) {
	for _, it := range items {
		mid := strings.TrimSpace(id(it))
		if mid != "" && seen[mid] {
			continue
		}
		kept = append(kept, it)
		if mid != "" {
			ids = append(ids, mid)
		}
	}
	return kept, ids
}
