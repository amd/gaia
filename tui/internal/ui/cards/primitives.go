package cards

import (
	"encoding/json"
	"fmt"
	"strings"
)

// renderCap is the contract's 500-item ceiling (§4.3): table rows, list items
// and key_value items are never considered beyond this many.
const renderCap = 500

// visibleRows is how many entries a primitive card actually draws, once its
// fixed chrome is accounted for. The contract's 500 is a ceiling, not a target —
// 500 rows in a terminal is not a card, it is a scroll trap that buries the rest
// of the transcript. Everything above the bound is reported, never dropped
// quietly: the truncation line counts ALL hidden entries, contract cap included.
func visibleRows(items, chrome int) int {
	n := maxCardRows - chrome
	if n < 1 {
		n = 1
	}
	if n > items {
		n = items
	}
	return n
}

// truncationLine reports every entry not drawn, whether the display bound or the
// contract cap hid it.
func truncationLine(total, shown int) string {
	if total <= shown {
		return ""
	}
	return "  +" + itoa(total-shown) + " more (truncated)"
}

// scalar decodes the contract's `string|number|boolean|null` cell type into the
// plain text the terminal shows. Markdown/HTML inside a value is NOT
// interpreted (§4.3) — it is printed exactly as it arrived.
type scalar struct{ text string }

func (s *scalar) UnmarshalJSON(b []byte) error {
	trimmed := strings.TrimSpace(string(b))
	switch trimmed {
	case "null":
		s.text = ""
		return nil
	case "true", "false":
		s.text = trimmed
		return nil
	}
	var str string
	if err := json.Unmarshal(b, &str); err == nil {
		s.text = clean(str)
		return nil
	}
	// Numbers keep their literal token. Decoding through float64 silently
	// rewrites the value the producer sent — 1234567890123456789 comes back as
	// …768, and 1e-7 as 0 — and §4.3 says the value renders as plain text, so
	// the token IS the text.
	if json.Valid(b) && isJSONNumber(trimmed) {
		s.text = trimmed
		return nil
	}
	return fmt.Errorf("value %s is not a string, number, boolean or null", truncTo(trimmed, 40))
}

func isJSONNumber(s string) bool {
	if s == "" {
		return false
	}
	for i, r := range s {
		switch {
		case r >= '0' && r <= '9':
		case r == '-' || r == '+':
		case r == '.' || r == 'e' || r == 'E':
		default:
			return false
		}
		if i == 0 && (r == '.' || r == 'e' || r == 'E' || r == '+') {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// table
// ---------------------------------------------------------------------------

type tablePayload struct {
	Title   string     `json:"title"`
	Columns []string   `json:"columns"`
	Rows    [][]scalar `json:"rows"`
}

func renderTable(data json.RawMessage, width int) string {
	var p tablePayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("table", err.Error(), data, width)
	}
	if len(p.Columns) == 0 {
		return renderInvalid("table", "columns is required and must be non-empty", data, width)
	}

	title := p.Title
	if strings.TrimSpace(title) == "" {
		title = "Table"
	}
	b := newBox(title, width)

	total := len(p.Rows)
	rows := p.Rows
	if len(rows) > renderCap {
		rows = rows[:renderCap]
	}
	// chrome: the column header row, its rule, and the truncation line.
	rows = rows[:visibleRows(len(rows), 3)]

	// A table with more columns than the terminal has room for renders every
	// cell as a bare "…" — technically not sheared, but no data and no hint that
	// anything was lost. Show the columns that can carry content and say so.
	columns := p.Columns
	hiddenCols := 0
	if maxCols := (b.inner() - 2 + 1) / (minColumnWidth + 1); len(columns) > maxCols && maxCols > 0 {
		hiddenCols = len(columns) - maxCols
		columns = columns[:maxCols]
	}

	widths := columnWidths(columns, rows, b.inner()-2)
	b.add("  " + joinCells(columns, widths))
	seps := make([]string, len(widths))
	for i, w := range widths {
		seps[i] = strings.Repeat("─", w)
	}
	b.add("  " + joinCells(seps, widths))
	for _, r := range rows {
		cells := make([]string, len(widths))
		for i := range widths {
			if i < len(r) {
				cells[i] = r[i].text
			}
		}
		b.add("  " + joinCells(cells, widths))
	}
	if len(rows) == 0 {
		b.add("  (no rows)")
	}
	if hiddenCols > 0 {
		b.add("  +" + itoa(hiddenCols) + " column(s) too narrow to show")
	}
	if line := truncationLine(total, len(rows)); line != "" {
		b.add(line)
	}
	return b.render()
}

// minColumnWidth is the narrowest a table column can be and still show anything
// beyond an ellipsis.
const minColumnWidth = 4

// columnWidths shares total columns out proportionally to the widest cell in
// each, so a narrow "id" column does not get the same space as a subject line.
func columnWidths(columns []string, rows [][]scalar, total int) []int {
	n := len(columns)
	gaps := n - 1
	avail := total - gaps
	if avail < n {
		avail = n
	}

	want := make([]int, n)
	sum := 0
	for i, c := range columns {
		want[i] = visualLen(c)
		for _, r := range rows {
			if i < len(r) {
				if w := visualLen(r[i].text); w > want[i] {
					want[i] = w
				}
			}
		}
		if want[i] < 1 {
			want[i] = 1
		}
		sum += want[i]
	}
	if sum <= avail {
		return want
	}

	out := make([]int, n)
	assigned := 0
	for i := range want {
		out[i] = want[i] * avail / sum
		if out[i] < 1 {
			out[i] = 1
		}
		assigned += out[i]
	}
	// Hand any rounding remainder to the widest column — it is the one most
	// likely to be a subject/message field where the extra cell is visible.
	for assigned < avail {
		widest, at := 0, 0
		for i := range out {
			if want[i]-out[i] > widest {
				widest, at = want[i]-out[i], i
			}
		}
		out[at]++
		assigned++
	}
	for assigned > avail {
		at := 0
		for i := range out {
			if out[i] > out[at] {
				at = i
			}
		}
		if out[at] <= 1 {
			break
		}
		out[at]--
		assigned--
	}
	return out
}

func joinCells(cells []string, widths []int) string {
	parts := make([]string, len(widths))
	for i, w := range widths {
		c := ""
		if i < len(cells) {
			c = cells[i]
		}
		parts[i] = padTo(truncTo(c, w), w)
	}
	return strings.TrimRight(strings.Join(parts, " "), " ")
}

// ---------------------------------------------------------------------------
// key_value
// ---------------------------------------------------------------------------

type keyValueItem struct {
	Key   string `json:"key"`
	Value scalar `json:"value"`
}

type keyValuePayload struct {
	Title string `json:"title"`
	// A pointer so an absent `items` is distinguishable from an explicit `[]`.
	// §4.3 marks only `title` optional, so a missing one is schema-invalid — not
	// an empty card that reads as "the agent found nothing".
	Items *[]keyValueItem `json:"items"`
}

func renderKeyValue(data json.RawMessage, width int) string {
	var p keyValuePayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("key_value", err.Error(), data, width)
	}
	if p.Items == nil {
		return renderInvalid("key_value", "items is required", data, width)
	}

	title := p.Title
	if strings.TrimSpace(title) == "" {
		title = "Details"
	}
	b := newBox(title, width)

	total := len(*p.Items)
	items := *p.Items
	if len(items) > renderCap {
		items = items[:renderCap]
	}
	items = items[:visibleRows(len(items), 1)]
	if len(items) == 0 {
		b.add("  (no items)")
		return b.render()
	}

	keyW := 0
	for _, it := range items {
		if w := visualLen(it.Key); w > keyW {
			keyW = w
		}
	}
	if max := (b.inner() - 4) / 2; keyW > max && max > 0 {
		keyW = max
	}
	valW := b.inner() - 2 - keyW - 2
	if valW < 1 {
		valW = 1
	}
	for _, it := range items {
		lines := wrap(it.Value.text, valW)
		b.add("  " + padTo(truncTo(it.Key, keyW), keyW) + "  " + lines[0])
		for _, cont := range lines[1:] {
			b.add("  " + strings.Repeat(" ", keyW) + "  " + cont)
		}
	}
	if line := truncationLine(total, len(items)); line != "" {
		b.add(line)
	}
	return b.render()
}

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

type listPayload struct {
	Title   string `json:"title"`
	Ordered bool   `json:"ordered"`
	// Pointer for the same reason as key_value: absent is invalid, empty is a
	// legitimate empty card.
	Items *[]scalar `json:"items"`
}

func renderList(data json.RawMessage, width int) string {
	var p listPayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("list", err.Error(), data, width)
	}
	if p.Items == nil {
		return renderInvalid("list", "items is required", data, width)
	}

	title := p.Title
	if strings.TrimSpace(title) == "" {
		title = "List"
	}
	b := newBox(title, width)

	total := len(*p.Items)
	items := *p.Items
	if len(items) > renderCap {
		items = items[:renderCap]
	}
	items = items[:visibleRows(len(items), 1)]
	if len(items) == 0 {
		b.add("  (no items)")
		return b.render()
	}

	// Markers are ASCII: "-" and "N." both render in every terminal font, which
	// a bullet glyph does not reliably do.
	markerW := 1
	if p.Ordered {
		markerW = visualLen(itoa(len(items))) + 1
	}
	for i, it := range items {
		marker := "-"
		if p.Ordered {
			marker = itoa(i+1) + "."
		}
		lines := wrap(it.text, b.inner()-2-markerW-1)
		b.add("  " + padTo(marker, markerW) + " " + lines[0])
		for _, cont := range lines[1:] {
			b.add("  " + strings.Repeat(" ", markerW) + " " + cont)
		}
	}
	if line := truncationLine(total, len(items)); line != "" {
		b.add(line)
	}
	return b.render()
}

// ---------------------------------------------------------------------------
// image
// ---------------------------------------------------------------------------

type imagePayload struct {
	Src     string `json:"src"`
	Alt     string `json:"alt"`
	Caption string `json:"caption"`
}

// renderImage degrades rather than draws: `image.src` is inline base64 raster
// (§4.3 forbids SVG and remote URLs), which a terminal cannot display. A
// sixel/kitty path is deliberately out of scope, so the caption carries the
// information and the card says plainly why there is no picture.
func renderImage(data json.RawMessage, width int) string {
	var p imagePayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("image", err.Error(), data, width)
	}
	if !isInlineRaster(p.Src) {
		return renderInvalid("image", "src must be an inline base64 raster data: URI", data, width)
	}

	// Both caption and alt when both exist: the picture is never drawn, so every
	// word describing it is the only thing the reader gets.
	label := strings.Join(dedupeNonEmpty(p.Caption, p.Alt), " — ")
	if label == "" {
		label = "untitled"
	}
	b := newBox("Image", width)
	b.addWrapped("  ", "[image: "+label+"]")
	b.addWrapped("  ", "Not shown — a terminal cannot display it.")
	return b.render()
}

// isInlineRaster enforces §4.3's src allowlist:
// ^data:image/(png|jpe?g|gif|webp);base64,. SVG is excluded deliberately (it can
// carry script) and remote URLs are rejected, so a prefix check on "data:image/"
// is NOT sufficient — it lets data:image/svg+xml through.
func isInlineRaster(src string) bool {
	for _, mime := range []string{"png", "jpeg", "jpg", "gif", "webp"} {
		if strings.HasPrefix(src, "data:image/"+mime+";base64,") {
			return true
		}
	}
	return false
}

func dedupeNonEmpty(vals ...string) []string {
	var out []string
	for _, v := range vals {
		v = strings.TrimSpace(v)
		if v == "" {
			continue
		}
		dup := false
		for _, seen := range out {
			if seen == v {
				dup = true
				break
			}
		}
		if !dup {
			out = append(out, v)
		}
	}
	return out
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}
