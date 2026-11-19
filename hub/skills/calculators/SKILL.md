---
name: calculators
description: Show a tip or loan calculator card that does the math. Use for tipping, splitting a bill, mortgages, car loans, monthly payments and loan interest.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    requirements:
      python: ">=3.10"
    tools:
      - name: show_tip_calculator
        description: Show a tip calculator that works out the tip, total and per-person share.
        parameters:
          bill:
            type: number
            required: true
          tip_percent:
            type: number
            required: false
            default: 18
          people:
            type: integer
            required: false
            default: 1
          currency:
            type: string
            required: false
            default: USD
          title:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
      - name: show_loan_calculator
        description: Show a loan calculator that works out the monthly payment, total paid and interest.
        parameters:
          price:
            type: number
            required: true
          annual_rate_percent:
            type: number
            required: true
          years:
            type: number
            required: true
          down_payment:
            type: number
            required: false
            default: 0
          currency:
            type: string
            required: false
            default: USD
          title:
            type: string
            required: false
            default: ""
        returns:
          type: object
        atomic: true
    provenance:
      source: starter-pack
---

# Calculators

Draws a `calculator` card in the Agent UI and the TUI. The card does the
arithmetic and recomputes as the user changes the numbers, so a wrong sum from
the model never reaches the answer.

## Procedure

1. **Pick the calculator.**
   - Tipping or splitting a bill → `calculators/show_tip_calculator` (bill,
     tip percent, people).
   - A mortgage, car loan, monthly payment or loan interest →
     `calculators/show_loan_calculator` (price, yearly rate in percent, years,
     down payment).
2. **Pass the numbers the user gave, as they gave them.** Send the full price as
   `price` and the part paid up front as `down_payment`; never subtract one
   from the other. Use defaults for what the user left out (18% tip, one
   person, no down payment).
3. **Set `currency` from the money in this request**: "$" is `USD`, "€" is
   `EUR`, "£" is `GBP`.
4. **If the call returns `"ok": false`, fix what its `error` names and call the
   same tool again.**
5. **After the card is shown, reply with one or two short sentences.** Do not do
   the math in text or state the results: the card shows them.
