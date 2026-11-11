package cards

import (
	"encoding/json"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// calculator is contract §4.3's tip/loan card. The agent sends only the
// starting numbers and tells the model not to state results, so the card
// computes them here with the same formulas the Agent UI uses.
type calculatorPayload struct {
	Calculator string                      `json:"calculator"`
	Title      string                      `json:"title"`
	Currency   string                      `json:"currency"`
	Inputs     map[string]*json.RawMessage `json:"inputs"`
}

type calcField struct {
	key, label, format string
	max                float64
	integer            bool
}

var calcFields = map[string][]calcField{
	"tip": {
		{"bill", "Bill", "currency", 1e9, false},
		{"tipPercent", "Tip", "percent", 100, false},
		{"people", "People", "count", 50, true},
	},
	"loan": {
		{"amount", "Price", "currency", 1e10, false},
		{"downPayment", "Down payment", "currency", 1e10, false},
		{"annualRatePercent", "Interest rate", "percent", 50, false},
		{"years", "Term", "years", 50, false},
	},
}

var calcDefaultTitle = map[string]string{"tip": "Tip calculator", "loan": "Loan calculator"}

var isoCurrency = regexp.MustCompile(`^[A-Z]{3}$`)

var currencySymbols = map[string]string{"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}

type calcLine struct {
	label  string
	value  float64
	format string
}

func renderCalculator(data json.RawMessage, width int) string {
	var p calculatorPayload
	if err := json.Unmarshal(data, &p); err != nil {
		return renderInvalid("calculator", err.Error(), data, width)
	}
	fields, ok := calcFields[p.Calculator]
	if !ok {
		return renderInvalid("calculator", "unknown calculator \""+clean(p.Calculator)+"\"", data, width)
	}
	if !isoCurrency.MatchString(p.Currency) {
		return renderInvalid("calculator", "currency must be a 3-letter ISO code", data, width)
	}
	in := map[string]float64{}
	for _, f := range fields {
		rawVal := p.Inputs[f.key]
		var v float64
		if rawVal == nil || json.Unmarshal(*rawVal, &v) != nil {
			return renderInvalid("calculator", "inputs."+f.key+" must be a number", data, width)
		}
		if math.IsNaN(v) || v < 0 || v > f.max || (f.integer && v != math.Trunc(v)) {
			return renderInvalid("calculator", "inputs."+f.key+" is out of range", data, width)
		}
		in[f.key] = v
	}

	var results []calcLine
	switch p.Calculator {
	case "tip":
		if in["bill"] <= 0 || in["people"] < 1 {
			return renderInvalid("calculator", "bill must be above 0 and people at least 1", data, width)
		}
		tip := in["bill"] * in["tipPercent"] / 100
		total := in["bill"] + tip
		results = []calcLine{{"Tip", tip, "currency"}, {"Total", total, "currency"}}
		if in["people"] > 1 {
			results = append(results, calcLine{"Per person", total / in["people"], "currency"})
		}
	case "loan":
		if in["amount"] <= 0 || in["years"] < 1 || in["downPayment"] >= in["amount"] {
			return renderInvalid("calculator", "price must be above 0 and above the down payment, and the term at least 1 year", data, width)
		}
		principal := in["amount"] - in["downPayment"]
		months := math.Round(in["years"] * 12)
		rate := in["annualRatePercent"] / 100 / 12
		monthly := principal / months
		if rate != 0 {
			monthly = principal * rate / (1 - math.Pow(1+rate, -months))
		}
		totalPaid := monthly * months
		results = []calcLine{
			{"Monthly payment", monthly, "currency"},
			{"Loan amount", principal, "currency"},
			{"Total paid", totalPaid, "currency"},
			{"Total interest", totalPaid - principal, "currency"},
		}
	}

	title := strings.TrimSpace(p.Title)
	if title == "" {
		title = calcDefaultTitle[p.Calculator]
	}
	b := newBox(title, width)
	var lines []calcLine
	for _, f := range fields {
		lines = append(lines, calcLine{f.label, in[f.key], f.format})
	}
	addCalcLines(b, lines, p.Currency)
	b.blank()
	addCalcLines(b, results, p.Currency)
	return b.render()
}

func addCalcLines(b *box, lines []calcLine, currency string) {
	labelW := 0
	for _, l := range lines {
		if w := visualLen(l.label); w > labelW {
			labelW = w
		}
	}
	for _, l := range lines {
		b.add("  " + padTo(l.label, labelW) + "  " + formatCalcValue(l.value, l.format, currency))
	}
}

func formatCalcValue(v float64, format, currency string) string {
	switch format {
	case "currency":
		amount := groupThousands(strconv.FormatFloat(math.Abs(v), 'f', 2, 64))
		sign := ""
		if v < 0 && amount != "0.00" {
			sign = "-"
		}
		if sym, ok := currencySymbols[currency]; ok {
			return sign + sym + amount
		}
		return sign + currency + " " + amount
	case "percent":
		return trimNumber(v) + "%"
	case "years":
		if v == 1 {
			return "1 year"
		}
		return trimNumber(v) + " years"
	default:
		return trimNumber(v)
	}
}

// trimNumber prints up to two decimals without trailing zeros.
func trimNumber(v float64) string {
	s := strconv.FormatFloat(v, 'f', 2, 64)
	s = strings.TrimRight(strings.TrimRight(s, "0"), ".")
	return groupThousands(s)
}

func groupThousands(s string) string {
	intPart, frac := s, ""
	if i := strings.IndexByte(s, '.'); i >= 0 {
		intPart, frac = s[:i], s[i:]
	}
	var out []byte
	for i := range intPart {
		if i > 0 && (len(intPart)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, intPart[i])
	}
	return string(out) + frac
}
