package cards

import (
	"strings"
	"testing"
)

func TestTipCalculatorComputesTheSplit(t *testing.T) {
	out := Render("calculator", raw(t, `{"kind":"calculator","calculator":"tip","title":"",
	  "currency":"USD","inputs":{"bill":142,"tipPercent":20,"people":4}}`), width80)
	t.Logf("\n%s", plain(out))
	assertWidth(t, out, width80)
	assertContains(t, out, "Tip calculator", "$142.00", "20%", "$28.40", "$170.40", "Per person", "$42.60")
}

func TestTipCalculatorForOneHasNoPerPersonLine(t *testing.T) {
	out := Render("calculator", raw(t, `{"calculator":"tip","title":"Lunch","currency":"EUR",
	  "inputs":{"bill":50,"tipPercent":10,"people":1}}`), width80)
	assertContains(t, out, "Lunch", "€5.00", "€55.00")
	assertNotContains(t, out, "Per person")
}

func TestLoanCalculatorMatchesTheAgentUI(t *testing.T) {
	// 300k price, 60k down, 6.5% over 30 years: the standard amortization
	// payment on 240k is 1,516.96.
	out := Render("calculator", raw(t, `{"calculator":"loan","title":"House","currency":"USD",
	  "inputs":{"amount":300000,"annualRatePercent":6.5,"years":30,"downPayment":60000}}`), width80)
	t.Logf("\n%s", plain(out))
	assertContains(t, out, "House", "$300,000.00", "$60,000.00", "6.5%", "30 years",
		"Monthly payment", "$1,516.96", "$240,000.00", "$546,106.77", "$306,106.77")
}

func TestLoanCalculatorZeroRateSplitsEvenly(t *testing.T) {
	out := Render("calculator", raw(t, `{"calculator":"loan","title":"","currency":"CHF",
	  "inputs":{"amount":12000,"annualRatePercent":0,"years":1,"downPayment":0}}`), width80)
	assertContains(t, out, "Loan calculator", "CHF 1,000.00", "1 year", "CHF 0.00")
}

func TestCalculatorBadPayloadSaysSoInsteadOfCrashing(t *testing.T) {
	for name, body := range map[string]string{
		"unknown calculator": `{"calculator":"bmi","currency":"USD","inputs":{}}`,
		"bad currency":       `{"calculator":"tip","currency":"$","inputs":{"bill":1,"tipPercent":1,"people":1}}`,
		"missing input":      `{"calculator":"tip","currency":"USD","inputs":{"bill":1,"people":1}}`,
		"string input":       `{"calculator":"tip","currency":"USD","inputs":{"bill":"1+1","tipPercent":1,"people":1}}`,
		"fractional people":  `{"calculator":"tip","currency":"USD","inputs":{"bill":1,"tipPercent":1,"people":1.5}}`,
		"zero bill":          `{"calculator":"tip","currency":"USD","inputs":{"bill":0,"tipPercent":1,"people":1}}`,
		"down over price":    `{"calculator":"loan","currency":"USD","inputs":{"amount":10,"annualRatePercent":1,"years":1,"downPayment":10}}`,
		"negative rate":      `{"calculator":"loan","currency":"USD","inputs":{"amount":10,"annualRatePercent":-1,"years":1,"downPayment":0}}`,
		"no inputs":          `{"calculator":"loan","currency":"USD"}`,
	} {
		out := Render("calculator", raw(t, body), width80)
		if !strings.Contains(plain(out), "Invalid calculator payload") {
			t.Errorf("%s: want the invalid-payload card, got\n%s", name, plain(out))
		}
	}
}
