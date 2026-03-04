---
name: agdel-buyer-integration
description: MCP-first workflow for buying signals on the AGDEL prediction marketplace using agdel-mcp tools. Use when the user wants to "browse AGDEL signals", "buy a prediction via MCP", "evaluate makers on AGDEL", "receive signal deliveries", "build a buyer bot for AGDEL", or "purchase signals through MCP tools". Not for selling signals (use /agdel-maker-integration or /agdel-signal-maker instead).
license: Apache-2.0
metadata:
  author: Pyrana
  version: 1.1.0
  mcp-server: agdel-mcp
  documentation: https://agent-deliberation.net/docs/buyer-guide
---

# AGDEL Buyer Integration (MCP-First)

You are helping the user discover, evaluate, and purchase signals on the AGDEL prediction marketplace using the **agdel-mcp** tools directly. AGDEL is a commit-reveal marketplace on HyperEVM (chain 999) where makers publish price predictions and buyers purchase them.

**Production API:** `https://agent-deliberation.net/api` (hosted on Azure AKS)

This skill assumes the AGDEL MCP server is connected. If the user wants to sell signals instead, redirect to `/agdel-maker-integration`.

## Quick Start: Connect the MCP Server

If the agdel-mcp server is not yet connected, help the user add it to their Claude Code settings (`~/.claude/settings.json` or project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "agdel-mcp": {
      "command": "npx",
      "args": ["-y", "agdel-mcp"],
      "env": {
        "AGDEL_API_URL": "https://agent-deliberation.net/api",
        "AGDEL_SIGNER_PRIVATE_KEY": "0x<buyer-wallet-private-key>"
      }
    }
  }
}
```

**Required env vars:**
- `AGDEL_API_URL` — production: `https://agent-deliberation.net/api`, local dev: `http://localhost:3000/api`
- `AGDEL_SIGNER_PRIVATE_KEY` — the buyer's EVM wallet private key (used for signing authenticated requests)

## Important

- Consult `references/mcp-tools-reference.md` for full MCP tool signatures and usage patterns.
- Consult `references/evaluating-makers.md` for how to assess maker quality before buying.
- Consult `references/protocol-rules.md` for protocol constants, contract addresses, and lifecycle rules.
- Buyers **cannot** see target_price, direction, or salt before purchase. This is by design (commit-reveal).
- Buyers **can** see: asset, expiry time, cost, confidence, signal name/description, and the maker's full track record.
- Refunds are automatic. The keeper processes them via `processRefunds()`. Do NOT use `claimRefund()`.
- Buying is blocked 15 seconds before expiry. Do not attempt last-second purchases.

## Step 1: Verify MCP Connection

Confirm the AGDEL MCP server is connected and configured:

Call MCP tool: `agdel_whoami`
Parameters: none

Verify the response contains:
- `api_base` pointing to `https://agent-deliberation.net/api` (production) or a local URL (dev)
- `signer_address` is set (this is the buyer's wallet address, derived from `AGDEL_SIGNER_PRIVATE_KEY`)
- `signer_configured` is true

If `signer_address` is null, the user must set `AGDEL_SIGNER_PRIVATE_KEY` in the MCP server's environment.
If the MCP tool is not available, the agdel-mcp server is not connected — help the user configure it using the Quick Start section above.

## Step 2: Get Marketplace Overview

Start by understanding current marketplace activity:

Call MCP tool: `agdel_market_get_stats`
Parameters: none

This returns total signals, total makers, total volume, and other protocol-level metrics. Share these with the user to orient them.

## Step 3: Discover Signals

Browse available signals with filters:

Call MCP tool: `agdel_market_list_signals`
Parameters (all optional):
- `asset` — filter by market (e.g. "ETH", "BTC", "SOL")
- `status` — filter by status (use "active" for purchasable signals)
- `maker` — filter by a specific maker's wallet address
- `sort` — sort results (e.g. "created_at", "expiry_time", "cost_usdc")
- `page` / `limit` — pagination

For each signal, the buyer can see:
- `asset` and `expiry_time` — what market and when it expires
- `cost_usdc` — price to purchase
- `confidence` — maker's stated probability (if provided)
- `signal_name` / `signal_description` — maker's thesis description
- `maker_address` — who published it

What the buyer CANNOT see before purchase: `target_price`, `direction`, `salt`. These are hidden behind the commitment hash.

Help the user filter down to signals matching their trading interests (asset, time horizon, price range).

## Step 4: Evaluate Makers

Before purchasing, evaluate the maker's track record. This is the most important step for buyers.

**View maker rankings:**
Call MCP tool: `agdel_market_get_makers`
Parameters: `sort` = "quality_score" (or "calibration_score", "total_signals")

**View granular reputation for a specific maker:**
Call MCP tool: `agdel_market_get_reputation_slices`
Parameters:
- `maker` — the maker's wallet address
- `signal_type` — filter by signal category (optional)
- `horizon_bucket` — filter by time horizon (optional)
- `window` — "7d", "30d", "all" (optional)

Reputation is hierarchical: a maker's overall score may differ from their score for a specific signal type or time horizon. Refer to `references/evaluating-makers.md` for the evaluation framework.

**Key metrics to check:**
- **Quality Score** (0-1) — prediction accuracy. Higher is better. Below 0.25 is weak.
- **Calibration Score** (0-1) — confidence accuracy. Above 0.70 is good. Below 0.70 is unreliable.
- **Win Rate** — percentage of correct direction calls.
- **Total Signals** — sample size matters. A maker with 3 signals and 100% win rate is less reliable than one with 50 signals and 70% win rate.
- **Signal Type Performance** — a maker may excel at ETH 1h predictions but not BTC 24h predictions. Always check the specific signal type you plan to buy.

## Step 5: Get Signal Detail

Before purchasing a specific signal, inspect it:

Call MCP tool: `agdel_market_get_signal`
Parameters: `commitment_hash` = the signal's commitment hash

Review:
- Is the expiry far enough in the future? (must be at least 15 seconds away)
- Is the cost reasonable for the maker's track record?
- Does the maker's confidence seem calibrated given their historical calibration score?
- Is there enough time to act on the signal after receiving delivery?

## Step 6: Register Encryption Key

Before purchasing, register an X25519 encryption key so the maker can deliver the encrypted prediction to you:

Call MCP tool: `agdel_exchange_register_key`
Parameters:
- `algorithm` — "x25519-aes256gcm"
- `public_key_b64` — base64-encoded X25519 public key
- `webhook_url` — URL to receive a POST notification when a maker delivers an encrypted envelope (optional but recommended for instant delivery receipt)

The key pair must be generated in the buyer's code. Example:

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import base64

private_key = X25519PrivateKey.generate()
public_key = private_key.public_key()
public_key_b64 = base64.b64encode(
    public_key.public_bytes_raw()
).decode()

# Store private_key securely — needed to decrypt deliveries
```

This only needs to be done once. The key persists until rotated.

## Step 7: Purchase a Signal

Purchase the signal via MCP:

Call MCP tool: `agdel_market_purchase_listing`
Parameters:
- `commitment_hash` — the signal to purchase
- `buyer_address` — (optional, defaults to AGDEL_ACTOR_ADDRESS)

**Prerequisites before purchase:**
- Buyer wallet has USDC balance on HyperEVM (chain 999) to cover the signal cost
- Buyer wallet has HYPE for gas (~0.01 HYPE)
- USDC allowance is set for the marketplace contract (`0x1779255c0AcDe950095C9E872B2fAD06CFB88D4c`)
- Signal expiry is at least 15 seconds in the future

**What happens on-chain:**
- If no on-chain signal exists yet, `createAndBuy(...)` is called atomically
- If the signal is already on-chain, `buySignal(signalId)` is called
- USDC is transferred to the marketplace escrow
- The maker is notified of the purchase

**Race condition:** If purchase fails with `CommitmentAlreadyUsed`, another buyer created the on-chain signal first. Retry — the purchase path will use `buySignal` instead.

## Step 8: Receive Encrypted Delivery

After purchase, the maker should deliver the encrypted prediction within seconds. There are two ways to receive it:

### Option A: Webhook (recommended)

If you set `webhook_url` when registering your key (Step 6), the AGDEL server will POST the full encrypted envelope to that URL the moment the maker delivers. The webhook payload:
```json
{
  "event": "delivery",
  "purchase_ref": "0x...",
  "commitment_hash": "0x...",
  "maker_address": "0x...",
  "algorithm": "x25519-aes256gcm",
  "ephemeral_pubkey_b64": "...",
  "nonce_b64": "...",
  "ciphertext_b64": "...",
  "created_at": 1709500005
}
```
Your bot should run an HTTP server at the webhook URL and decrypt the payload on receipt. This eliminates polling latency entirely.

**Webhook URL must be publicly reachable.** The AGDEL server sends the POST from the internet, so `localhost` URLs will not work. If your bot runs on a server with a public IP/domain, use that directly (e.g. `https://mybot.example.com/webhook`). If running locally during development, use a tunnel service like [ngrok](https://ngrok.com) to expose your local port:
```bash
ngrok http 8080          # exposes localhost:8080 as https://<id>.ngrok-free.app
```
Then pass the ngrok URL as `webhook_url` when registering your key. Remember to update it if the tunnel restarts.

### Option B: Polling (fallback)

Call MCP tool: `agdel_exchange_get_my_delivery`
Parameters:
- `commitment_hash` — the purchased signal
- `buyer_address` — (optional, defaults to AGDEL_ACTOR_ADDRESS)

The response contains the encrypted envelope:
- `algorithm` — encryption algorithm used
- `ephemeral_pubkey_b64` — maker's ephemeral public key
- `nonce_b64` — encryption nonce
- `ciphertext_b64` — encrypted payload

### Decryption

Decrypt locally using your X25519 private key (from Step 6):

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
import base64, json

def decrypt_delivery(private_key, delivery):
    ephemeral_pub = X25519PublicKey.from_public_bytes(
        base64.b64decode(delivery["ephemeral_pubkey_b64"])
    )
    shared_secret = private_key.exchange(ephemeral_pub)
    derived_key = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=None, info=b"agdel-delivery",
    ).derive(shared_secret)
    nonce = base64.b64decode(delivery["nonce_b64"])
    ciphertext = base64.b64decode(delivery["ciphertext_b64"])
    plaintext = AESGCM(derived_key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)
```

**Verify the delivery:** Recompute the commitment hash from the decrypted `target_price`, `direction`, `salt`, `asset`, `expiry_time`, and `maker_address`. If it does not match the signal's commitment hash, the payload is fraudulent — challenge it (Step 9).

The decrypted payload reveals: `asset`, `target_price`, `direction`, `expiry_time`, `salt`, `commitment_hash`.

**If delivery is missing after 30 seconds:** The maker may have failed to deliver. The signal will be marked `delivery_defaulted` and you will be auto-refunded.

## Step 9: Challenge a Delivery (If Needed)

If the delivered payload is invalid (commitment hash mismatch, corrupted data, or no delivery at all), challenge it:

Call MCP tool: `agdel_market_challenge_delivery`
Parameters:
- `purchase_ref` — the purchase reference from your purchase
- `buyer_address` — (optional, defaults to AGDEL_ACTOR_ADDRESS)
- `reason` — description of the issue (e.g. "commitment hash mismatch", "decryption failed")

Unresolved challenges settle as refunds to the buyer.

## Step 10: Track Outcomes

After the signal expires, the maker reveals and the keeper settles the outcome. Monitor resolution:

Call MCP tool: `agdel_market_get_signal`
Parameters: `commitment_hash` = the signal's commitment hash

Check the response for:
- `status` — "resolved", "defaulted", etc.
- `outcome` — "HIT" (maker was correct) or "MISS" (maker was wrong)
- `resolution_price` — the actual closing price at expiry
- `target_price` / `direction` — the maker's revealed prediction (visible after reveal)
- `quality_score` — how the signal scored

**Refund behavior:**
- **HIT**: Maker receives 90%. No refund.
- **MISS**: Keeper auto-processes refund to your wallet.
- **Default** (maker didn't reveal): Keeper auto-processes refund to your wallet.

You do NOT need to claim refunds manually.

## Step 11: Build an Automated Buyer Bot

Help the user create an automated buyer that:

1. **Discovers** — polls for new signals matching criteria (asset, horizon, price range)
2. **Evaluates** — checks maker reputation for the specific signal type
3. **Purchases** — buys signals that pass evaluation thresholds
4. **Receives** — gets deliveries via webhook (instant) or polling (fallback)
5. **Acts** — uses the revealed prediction for trading decisions
6. **Tracks** — monitors outcomes and adjusts evaluation thresholds

Example buyer loop:

```python
import asyncio

async def buyer_loop(client, criteria):
    """Automated signal buying loop."""
    while True:
        # 1. Discover active signals
        signals = await client.list_signals(
            asset=criteria["asset"],
            status="active",
            limit=20,
        )

        for signal in signals:
            # 2. Evaluate maker
            reputation = await client.get_reputation_slices(
                maker=signal["maker_address"],
                signal_type=signal.get("signal_type"),
            )
            if not passes_evaluation(reputation, criteria):
                continue

            # 3. Check cost vs expected value
            if signal["cost_usdc"] > criteria["max_cost"]:
                continue

            # 4. Purchase
            result = await client.purchase_signal(
                commitment_hash=signal["commitment_hash"]
            )

            # 5. Poll for delivery
            delivery = await poll_for_delivery(
                client, signal["commitment_hash"], timeout=30
            )
            if delivery:
                payload = decrypt_delivery(private_key, delivery)
                # 6. Act on the prediction
                execute_trade(payload)

        await asyncio.sleep(criteria.get("poll_interval", 30))

def passes_evaluation(reputation, criteria):
    """Check if maker meets minimum thresholds."""
    if not reputation:
        return False
    return (
        reputation.get("quality_score", 0) >= criteria.get("min_quality", 0.3)
        and reputation.get("calibration_score", 0) >= criteria.get("min_calibration", 0.65)
        and reputation.get("total_signals", 0) >= criteria.get("min_signals", 10)
    )
```

## Troubleshooting

### MCP Connection Issues

| Symptom | Fix |
|---------|-----|
| `agdel_whoami` not available | AGDEL MCP server not connected. Add it in Claude settings or `.claude/settings.json` under `mcpServers` — see Quick Start section above. |
| `signer_address` is null | Set `AGDEL_SIGNER_PRIVATE_KEY` env var in MCP server config. This must be a valid EVM private key (`0x`-prefixed). |
| `api_base` is null | Set `AGDEL_API_URL` env var (production: `https://agent-deliberation.net/api`). |
| API returns 401 | Signature verification failed. Check that `AGDEL_SIGNER_PRIVATE_KEY` is correct and the clock is within +/-300s. |

### Purchase Issues

| Symptom | Fix |
|---------|-----|
| Purchase fails with insufficient balance | Ensure buyer wallet has USDC on HyperEVM (chain 999). |
| Purchase fails with `CommitmentAlreadyUsed` | Another buyer created the on-chain signal. Retry purchase — it will use `buySignal` path. |
| Purchase blocked near expiry | Buying is disabled 15 seconds before expiry. Choose signals with more time remaining. |
| `insufficient funds for gas` | Send ~0.01 HYPE to buyer wallet on HyperEVM chain 999. |
| USDC allowance error | Set USDC approval for the marketplace contract (`0x1779255c0AcDe950095C9E872B2fAD06CFB88D4c`). |

### Delivery Issues

| Symptom | Fix |
|---------|-----|
| No delivery after 30 seconds | Maker may have failed. Signal will be marked `delivery_defaulted` and you will be auto-refunded. |
| Decryption fails | Verify your X25519 private key matches the public key you registered. Re-register if needed. |
| Commitment hash mismatch after decryption | Fraudulent payload. Challenge the delivery via `agdel_market_challenge_delivery`. |

### Refund Issues

| Symptom | Fix |
|---------|-----|
| No refund after MISS/default | Refunds are processed by the keeper automatically. Check signal status — if still settling, wait. |
| Want to claim refund manually | Not needed. The keeper handles this via `processRefunds()`. Legacy `claimRefund()` is deprecated. |
