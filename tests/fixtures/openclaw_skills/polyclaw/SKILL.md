---
name: polyclaw
description: "Trade on Polymarket via split + CLOB execution. Browse markets, track positions with P&L, discover hedges via LLM. Polygon/Web3."
metadata: {"openclaw":{"emoji":"🦞","homepage":"https://polymarket.com","primaryEnv":"POLYCLAW_PRIVATE_KEY","requires":{"bins":["uv"],"env":["CHAINSTACK_NODE","POLYCLAW_PRIVATE_KEY"]},"install":[{"id":"uv-brew","kind":"brew","formula":"uv","bins":["uv"],"label":"Install uv (brew)"}]},"clawdbot":{"emoji":"🦞","homepage":"https://polymarket.com","primaryEnv":"POLYCLAW_PRIVATE_KEY","requires":{"bins":["uv"],"env":["CHAINSTACK_NODE","POLYCLAW_PRIVATE_KEY"]},"install":[{"id":"uv-brew","kind":"brew","formula":"uv","bins":["uv"],"label":"Install uv (brew)"}]}}
---

# PolyClaw

Trading-enabled Polymarket skill for OpenClaw. Browse markets, manage wallets, execute trades, and track positions.

## Features

- **Market Browsing** - Search and browse Polymarket prediction markets
- **Wallet Management** - Env-var based wallet configuration
- **Trading** - Buy YES/NO positions via split + CLOB execution
- **Position Tracking** - Track entry prices, current prices, and P&L
- **Hedge Discovery** - LLM-powered covering portfolio discovery via logical implications

## Quick Start

First, install dependencies (from skill directory):

```bash
cd {baseDir}
uv sync
```

### First-Time Setup (Required for Trading)

Before your first trade, set Polymarket contract approvals (one-time, costs ~0.01 POL in gas):

```bash
uv run python scripts/polyclaw.py wallet approve
```

This submits 6 approval transactions to Polygon. You only need to do this once per wallet.

### Browse Markets

```bash
# Trending markets by volume
uv run python scripts/polyclaw.py markets trending

# Search markets
uv run python scripts/polyclaw.py markets search "election"

# Market details (returns full JSON with all fields)
uv run python scripts/polyclaw.py market <market_id>
```

**Output options:**
- Default output is a formatted table (good for display)
- Use `--full` flag for full question text without truncation
- Use `--json` flag via `scripts/markets.py --json trending` for structured JSON output

### Wallet Management

```bash
# Check wallet status (address, balances)
uv run python scripts/polyclaw.py wallet status

# Set contract approvals (one-time)
uv run python scripts/polyclaw.py wallet approve
```

The wallet is configured via the `POLYCLAW_PRIVATE_KEY` environment variable.

### Trading

```bash
# Buy YES position for $50
uv run python scripts/polyclaw.py buy <market_id> YES 50

# Buy NO position for $25
uv run python scripts/polyclaw.py buy <market_id> NO 25
```

### Positions

```bash
# List all positions with P&L
uv run python scripts/polyclaw.py positions
```

### Hedge Discovery

Find covering portfolios - pairs of market positions that hedge each other via contrapositive logic.

```bash
# Scan trending markets for hedges
uv run python scripts/polyclaw.py hedge scan

# Scan markets matching a query
uv run python scripts/polyclaw.py hedge scan --query "election"

# Analyze specific markets for hedging relationship
uv run python scripts/polyclaw.py hedge analyze <market_id_1> <market_id_2>
```

**Output options:**
- Default output is a formatted table showing Tier, Coverage, Cost, Target, and Cover
- Use `--json` flag for structured JSON output
- Use `--min-coverage 0.90` to filter by minimum coverage (default 0.85)
- Use `--tier 1` to filter by tier (1=best, default 2)

**Coverage tiers:**
- **Tier 1 (HIGH):** >=95% coverage - near-arbitrage opportunities
- **Tier 2 (GOOD):** 90-95% - strong hedges
- **Tier 3 (MODERATE):** 85-90% - decent but noticeable risk
- **Tier 4 (LOW):** <85% - speculative (filtered by default)

**LLM model:** Uses `nvidia/nemotron-nano-9b-v2:free` via OpenRouter. Model selection matters — some models find spurious correlations while others (like DeepSeek R1) have output format issues. Override with `--model <model_id>` if needed.

## Security

For the MVP, the private key is stored in an environment variable for simplicity and Claude Code compatibility.

**Security Warning:** Keep only small amounts in this wallet. Withdraw regularly to a secure wallet.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CHAINSTACK_NODE` | Yes (trading) | Polygon RPC URL |
| `OPENROUTER_API_KEY` | Yes (hedge) | OpenRouter API key for LLM hedge discovery |
| `POLYCLAW_PRIVATE_KEY` | Yes (trading) | EVM private key (hex, with or without 0x prefix) |
| `HTTPS_PROXY` | Recommended | Rotating residential proxy for CLOB (e.g., IPRoyal) |
| `CLOB_MAX_RETRIES` | No | Max CLOB retries with IP rotation (default: 5) |
| `POLY_BUILDER_CODE` | No | Bytes32 builder code for attribution (from polymarket.com/settings?tab=builder). Attached to every order when set. |

**Security Warning:** Keep only small amounts in this wallet. Withdraw regularly to a secure wallet. The private key in an env var is convenient for automation but less secure than encrypted storage.

## Trading Flow

1. **Split Position** - pUSD is split into YES + NO tokens via CTF contract
2. **Sell Unwanted** - The unwanted side is sold via CLOB order book (V2)
3. **Result** - You hold the wanted position, recovered partial cost from selling unwanted

Example: Buy YES at $0.70
- Split $100 pUSD → 100 YES + 100 NO tokens
- Sell 100 NO tokens at ~$0.30 → recover ~$27 pUSD
- Net cost: ~$73 for 100 YES tokens (entry: $0.73)

If you only have USDC.e, wrap it 1:1 into pUSD via the Collateral Onramp's `wrap()` function before trading.

## Polymarket Contracts (Polygon Mainnet, CLOB V2)

- **pUSD (collateral):** `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`
- **USDC.e (onramp input):** `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- **Collateral Onramp:** `0x93070a847efEf7F70739046A929D47a521F5B8ee`
- **CTF (Conditional Tokens):** `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- **CTF Exchange (V2):** `0xE111180000d2663C0091e4f400237545B87B996B`
- **Neg Risk CTF Exchange (V2):** `0xe2222d279d744050d28e00520010520000310F59`

## Dependencies

Install with uv (from skill directory):
```bash
cd {baseDir}
uv sync
```

## Limitations

- Trading requires wallet approval setup (one-time)
- CLOB sells may fail if liquidity is insufficient

### CLOB Cloudflare Blocking

Polymarket's CLOB API uses Cloudflare protection that blocks POST requests from many IPs, including datacenter IPs and some residential ISPs. This affects the "sell unwanted tokens" step.

**Solution: Residential proxy with retry logic**

The recommended setup uses a rotating residential proxy (e.g., IPRoyal, BrightData). The CLOB client automatically retries with new IPs until one works:

```bash
export HTTPS_PROXY="http://user:pass@geo.iproyal.com:12321"
export CLOB_MAX_RETRIES=10  # Default is 5
```

With this setup, CLOB orders typically succeed within 5-10 retries as the proxy rotates through IPs until finding an unblocked one.

**Alternative workarounds:**
1. **Use `--skip-sell`** — Keep both YES and NO tokens, sell manually on polymarket.com
2. **No proxy** — Split still works; only CLOB sell is affected

If CLOB fails after all retries, your split still succeeded. The output tells you how many tokens to sell manually.

## Troubleshooting

### "No wallet available"
Set the `POLYCLAW_PRIVATE_KEY` environment variable:
```bash
export POLYCLAW_PRIVATE_KEY="0x..."
```

### "Insufficient pUSD"
Check balance with `uv run python scripts/polyclaw.py wallet status`. Trades require pUSD (Polymarket USD). If you have USDC.e, wrap it 1:1 via the Collateral Onramp (`0x9307...B8ee`) — the polymarket.com UI does this automatically; API users wrap manually.

### "CLOB order failed"
The CLOB sell may fail due to:
- Insufficient liquidity at the sell price
- IP blocked by Cloudflare (try proxy)

Your split still succeeded - you have the tokens, just couldn't sell unwanted side.

### "Approvals not set"
First trade requires contract approvals. Run:
```bash
uv run python scripts/polyclaw.py wallet approve
```

## License

Apache 2.0
