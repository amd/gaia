---
name: receipt-format
description: Log purchases as a receipt with a computed total when the user lists things they bought.
version: "1.2.0"
license: MIT
---

# Receipt Format

When the user lists purchases and asks you to log them:

1. Open with the exact header line `RECEIPT LOG`.
2. One line per item, in the order given: `- <item>: $<amount>`, amounts with
   exactly two decimals.
3. Then the line `TOTAL: $<sum>`, two decimals, summing only the listed items.
4. Close with the exact line `Logged by receipt-format.`

Never add items, tax, or tips the user did not state.
