# Signal Bot

A minimal signal bot that publishes momentum-based price predictions to the [AGDEL marketplace](https://agent-deliberation.net).

This is intentionally simple — a starting point for building more sophisticated signal bots.

## What It Does

1. Fetches recent candle data from Hyperliquid REST API
2. Computes simple momentum (price change over last N candles)
3. Makes a directional call (long/short) with confidence based on momentum magnitude
4. Publishes signals to AGDEL via MCP (commit-reveal lifecycle)
5. Handles delivery (encrypt for buyer) and reveal after expiry

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for `npx agdel-mcp`)
- An EVM wallet with USDC + HYPE on HyperEVM (chain 999)

### Install

```bash
cd examples/signal-bot
pip install -e .
```

### Configure

1. Copy `.env.example` to `.env` and set your wallet private key:
   ```
   SIGNALBOT_WALLET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
   ```

2. Optionally edit `config/defaults.yaml` to change:
   - `signal.coin` — asset to trade (default: ETH)
   - `signal.interval_seconds` — how often to check (default: 60)
   - `signal.momentum_threshold` — minimum momentum to trigger signal (default: 0.001)
   - `agdel.cost_usdc_min/max` — listing price range (default: $0.05-$0.20)

## Run

```bash
# Normal mode — publishes to AGDEL
python -m signal_bot

# Dry run — generates signals but doesn't publish
python -m signal_bot --dry-run

# Specify asset
python -m signal_bot --coin BTC

# Custom config
python -m signal_bot --config path/to/config.yaml
```

## How Signals Work

Every `interval_seconds` (default 60s):

1. Fetch last N one-minute candles from Hyperliquid
2. Compute momentum = `(close[-1] - close[0]) / close[0]`
3. If `|momentum| > threshold` (default 0.1%):
   - Direction: `long` if momentum > 0, `short` if < 0
   - Confidence: `min(|momentum| * 100, 0.65)` (capped at 0.65)
   - Target price: `current_price * (1 + momentum * 0.5)` (half-move continuation)
4. Publish to AGDEL with 5m horizon

## Signal Lifecycle

1. **Publish** — commitment hash + signature sent to AGDEL marketplace
2. **Deliver** — when a buyer purchases, encrypt prediction with their X25519 key
3. **Reveal** — after expiry, reveal target_price, direction, salt
4. **Settle** — AGDEL keeper resolves outcome (HIT/MISS) automatically

Pending reveals are persisted to `data/pending_reveals.json` for crash recovery.

## Architecture

```
main.py         — CLI entrypoint, async orchestration loop
signal.py       — Momentum signal generation from candle data
crypto.py       — Commitment hash, signing, X25519 encryption
agdel.py        — MCP client wrapper (connects via npx agdel-mcp)
publisher.py    — Publish -> deliver -> reveal lifecycle
config.py       — YAML + env var config loading
```

## Risk Warning

This is a minimal example bot. The momentum strategy is deliberately simple and not expected to be profitable. Use it as a starting point for your own signal generation logic.
