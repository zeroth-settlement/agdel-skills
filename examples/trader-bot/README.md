# Trader Bot

A minimal trading bot that buys signals from the [AGDEL marketplace](https://agent-deliberation.net), uses a 3x3 decision matrix to recommend trades, and lets you approve or reject them via a web dashboard.

**This bot can execute real trades on Hyperliquid. Paper mode is enabled by default. Proceed carefully.**

## How It Works

```
Every 5 seconds (tick loop):
  1. Fetch ETH mark price from Hyperliquid
  2. Get latest 5m and 15m signals from AGDEL
  3. Run matrix engine → recommended action
  4. Broadcast state to dashboard
  5. Wait for you to click "Approve" or "Reject"

Every 30 seconds (AGDEL poll loop):
  1. List active signals for ETH
  2. Filter by confidence, cost, maker reputation
  3. Buy top candidates (if auto-buy enabled) or wait for manual buy
  4. Decrypt deliveries and store for matrix engine
```

The key feature is **human-in-the-loop**: the matrix engine produces a recommendation (open long, increase, close, flip short, etc.) but never executes automatically. You must click Approve on the dashboard.

## Prerequisites

- Python 3.10+
- A funded wallet on HyperEVM (chain 999) for AGDEL signal purchases
- A Hyperliquid API wallet for trading (Claude can walk you through setting this up)
- Node.js 18+ (for `npx agdel-mcp`)

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Copy and configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your wallet keys
   ```

3. Review `config/trading.yaml`:
   - `trading.enable: false` — paper mode (default). Set to `true` for live trading.
   - `agdel.autoBuy: false` — manual signal buying via dashboard (default).
   - `matrix.signalHorizons` — which signal timeframes to use (5m fast, 15m slow).
   - `agdel.budget` — spending limits for signal purchases.

4. Start the server:
   ```bash
   python server.py
   ```

5. Open the dashboard at [http://localhost:9002](http://localhost:9002).

## Configuration

### Trading Modes

| Mode | Description |
|------|-------------|
| `paper` | Simulated trading with a $1000 starting balance. No real orders. |
| `live` | Real orders on Hyperliquid. **Uses real funds.** |

You can switch modes from the dashboard or via API:
```bash
curl -X POST http://localhost:9002/api/config/mode -H 'Content-Type: application/json' -d '{"mode": "paper"}'
```

### Matrix Engine

The 3x3 decision matrix combines fast (5m) and slow (15m) signal states:

| | Slow BULL | Slow FLAT | Slow BEAR |
|---|---|---|---|
| **Fast BULL** | open_long / increase | open_long (partial) | reduce / close |
| **Fast FLAT** | hold | hold | hold |
| **Fast BEAR** | reduce / close | open_short (partial) | open_short / increase |

Signal states are classified by score and confidence thresholds defined in `config/trading.yaml`.

### AGDEL Budget

Control signal spending with:
- `maxCostPerSignalUsdc` — max price per individual signal
- `maxHourlySpendUsdc` — hourly spending cap
- `maxDailySpendUsdc` — daily spending cap

Reset budget counters via dashboard or API:
```bash
curl -X POST http://localhost:9002/api/agdel/budget/reset
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/state` | Current system state |
| POST | `/api/approve` | Approve pending trade recommendation |
| POST | `/api/reject` | Reject pending trade recommendation |
| POST | `/api/config/mode` | Set paper/live mode |
| GET | `/api/trades` | Trade history |
| GET | `/api/agdel/available` | Available signals on marketplace |
| GET | `/api/agdel/purchases` | Purchase history |
| POST | `/api/agdel/buy` | Manually buy a signal by commitment hash |
| POST | `/api/agdel/budget/reset` | Reset budget counters |
| WS | `/ws` | Real-time state updates |

## Project Structure

```
trader-bot/
  server.py           # FastAPI app, tick loop, WebSocket, REST endpoints
  agdel_buyer.py      # MCP client for buying signals + decryption
  matrix_engine.py    # 3x3 decision matrix (deterministic)
  hl_trader.py        # Paper + live Hyperliquid trading
  dashboard.html      # Browser UI with approve/reject buttons
  config/
    trading.yaml      # All configuration
```

## Warnings

- **Paper mode** is the default. Do not enable live mode until you understand the risks.
- **Signal quality varies.** The AGDEL marketplace is permissionless — anyone can post signals. Use maker filters and budget limits.
- **This is example code.** It is intentionally minimal. Production trading systems need more robust error handling, position management, and risk controls.
- **You are responsible** for any trades executed. The approve button is your last line of defense.
