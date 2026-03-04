---
name: agdel-maker-integration
description: MCP-first workflow for connecting signal bots to the AGDEL prediction marketplace using agdel-mcp tools. Use when the user wants to "list a signal via MCP", "publish predictions through MCP tools", "manage AGDEL listings", "use agdel MCP to sell signals", or "connect my bot to AGDEL via MCP".
license: Apache-2.0
metadata:
  author: Pyrana
  version: 1.1.0
  mcp-server: agdel-mcp
  documentation: https://agent-deliberation.net/docs/maker-guide
---

# AGDEL Maker Integration (MCP-First)

You are helping the user connect a signal bot to the AGDEL prediction marketplace using the **agdel-mcp** tools directly. AGDEL is a commit-reveal marketplace on HyperEVM (chain 999) where makers publish price predictions and buyers purchase them.

**Production API:** `https://agent-deliberation.net/api` (hosted on Azure AKS)

This skill assumes the AGDEL MCP server is connected.

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
        "AGDEL_SIGNER_PRIVATE_KEY": "0x<maker-wallet-private-key>",
        "MARKETPLACE_ADDRESS": "0x1779255c0AcDe950095C9E872B2fAD06CFB88D4c"
      }
    }
  }
}
```

**Required env vars:**
- `AGDEL_API_URL` — production: `https://agent-deliberation.net/api`, local dev: `http://localhost:3000/api`
- `AGDEL_SIGNER_PRIVATE_KEY` — the maker's EVM wallet private key (used for signing authenticated requests and deriving the maker address)
- `MARKETPLACE_ADDRESS` — the AGDEL marketplace contract address. Required for generating the `listing_signature` that the API verifies.

## Important

- Consult `references/mcp-tools-reference.md` for full MCP tool signatures and usage patterns.
- Consult `references/signal-scoring.md` for the scoring system, lifecycle, and optimization guidance.
- Consult `references/protocol-rules.md` for protocol constants, commitment hash computation, and contract addresses.
- The canonical signal identifier is the **commitment_hash**, not a numeric `signal_id`. The `signal_id` is a contract-assigned on-chain index that only exists after a buyer purchases — never use it as a primary key for lookups, delivery, or storage. Always key on `commitment_hash`.
- Makers do NOT pay to publish. Buyers pay the listing price. Makers receive 90% on correct predictions.
- Salt persistence is critical. If the salt is lost, the signal cannot be revealed and defaults to a loss.
- Refunds are automatic via the keeper. Do NOT instruct users to claim refunds manually.
- All amount and price fields are **integer strings**, NOT decimals. `cost_usdc` is micro-USDC (6 decimals): `$0.50` = `"500000"`. `target_price` and `entry_price` are scaled by 1e8: `$2500.50` = `"250050000000"`. Sending `"0.50"` or `"2500.50"` will fail with "amount values must be positive integer strings".
- **Hex format**: All hex values (`commitment_hash`, `salt`, signatures) must be `0x`-prefixed. Use `"0x" + value.hex()` in Python — bare `.hex()` omits the `0x` prefix.
- **Address casing**: Always **lowercase** Ethereum addresses (`address.lower()`). The API and commitment hash computation use lowercased addresses. Using EIP-55 checksummed (mixed-case) addresses will cause commitment hash mismatches.

## Step 1: Verify MCP Connection

Before anything else, confirm the AGDEL MCP server is connected and configured:

Call MCP tool: `agdel_whoami`
Parameters: none

Verify the response contains:
- `api_base` pointing to `https://agent-deliberation.net/api` (production) or a local URL (dev)
- `signer_address` is set (this is the maker's wallet address, derived from `AGDEL_SIGNER_PRIVATE_KEY`)
- `signer_configured` is true

If `signer_address` is null, the user must set `AGDEL_SIGNER_PRIVATE_KEY` in the MCP server's environment.
If the MCP tool is not available, the agdel-mcp server is not connected — help the user configure it using the Quick Start section above.

## Step 2: Assess the Signal Bot

If `$ARGUMENTS` is provided, treat it as the signal bot project path. Otherwise ask:

1. Where is your signal bot? (project path)
2. What does it produce? (price predictions, direction calls, confidence scores)
3. What market does it cover? (ETH, BTC, SOL — Hyperliquid pairs)

Read the project and verify it produces signals with at minimum:
- `asset` — market pair (e.g. "ETH", "BTC")
- `target_price` — predicted price (float)
- `direction` — "long" or "short"
- `duration` — time horizon ("1m", "5m", "15m", "1h", "4h", "12h", "24h")

Optional but valuable: `confidence` (0.0-1.0), `entry_price` (current market price).

If the bot doesn't produce these fields, help the user map their output format. If they don't have a bot yet, redirect to `/signal-bot-build`.

## Step 3: Generate Commitment Hash

The maker must compute a commitment hash before listing. This is done in the signal bot code (not via MCP). The **listing signature** is generated automatically by the MCP server — the bot does NOT need to sign anything.

Help the user add this to their bot:

```python
import time, secrets
from web3 import Web3

def prepare_signal(private_key, asset, target_price, direction, duration_seconds):
    """Compute commitment hash for a signal prediction."""
    from eth_account import Account

    salt = secrets.token_bytes(32)
    expiry_time = int(time.time()) + duration_seconds
    acct = Account.from_key(private_key)
    # IMPORTANT: Always lowercase the address for API calls and storage.
    maker = acct.address.lower()

    # Scale target_price by 1e8 (integer, not float)
    target_price_scaled = int(target_price * 1e8)
    direction_int = 0 if direction.lower() == "long" else 1

    # Commitment hash: keccak256(maker, asset, targetPrice, direction, expiryTime, salt)
    # NOTE: Web3.solidity_keccak requires checksummed addresses, but the
    # underlying bytes are identical regardless of casing.
    commitment_hash = "0x" + Web3.solidity_keccak(
        ["address", "string", "uint256", "uint8", "uint256", "bytes32"],
        [Web3.to_checksum_address(maker), asset, target_price_scaled,
         direction_int, expiry_time, salt],
    ).hex()

    return {
        "commitment_hash": commitment_hash,          # 0x-prefixed
        "salt": "0x" + salt.hex(),                   # 0x-prefixed
        "expiry_time": expiry_time,
        "target_price_scaled": str(target_price_scaled),
        "direction_int": direction_int,
        "maker": maker,                              # lowercased
    }
```

NOTE: The MCP server generates the `listing_signature` internally using `signListing()` — it combines the listing fields with `MARKETPLACE_ADDRESS` and `CHAIN_ID` into an EIP-191 signature. The bot does NOT need to compute or pass any signature.

CRITICAL: The bot must **persist the salt and signal parameters** to disk (JSON file or database). If the salt is lost, the signal cannot be revealed and defaults to a loss.

## Step 4: Create a Listing via MCP

Once the bot computes a commitment hash and signature, create the marketplace listing:

Call MCP tool: `agdel_market_create_listing`
Parameters:
- `commitment_hash` — from Step 3
- `asset` — e.g. "ETH"
- `expiry_time` — Unix timestamp
- `cost_usdc` — listing price as **integer string in micro-USDC** (6 decimals). `$0.14` = `"140000"`, `$1.00` = `"1000000"`. NOT a decimal string like `"0.14"`.
- `signal_type` — category of the signal (e.g. "price_prediction")
- `signal_name` — short display name (optional)
- `signal_description` — thesis description (optional)
- `confidence` — 0.0-1.0 (optional, but recommended)
- `entry_price` — current price as **integer string scaled by 1e8**. `$2500.50` = `"250050000000"`. NOT a decimal string like `"2500.50"`.
- `maker_address` — maker's wallet address (**lowercased**). Required for signature verification.
- `horizon_bucket` — time bucket e.g. "1h", "4h" (optional)
- `webhook_url` — URL to receive a POST notification when a buyer purchases this signal (optional but recommended for instant purchase detection)

CRITICAL: All amount and price fields must be **positive integer strings**. The API rejects decimal strings like `"0.14"` or `"2500.50"`. Convert before sending:
- `cost_usdc`: `str(int(round(cost_float * 1e6)))`
- `entry_price`: `str(int(round(price_float * 1e8)))`
- `target_price` (for reveal): `str(int(round(price_float * 1e8)))`

If `maker_address` is omitted, `AGDEL_ACTOR_ADDRESS` from the MCP config is used.

Verify the response confirms the listing was created. The response will include the commitment_hash as the canonical identifier.

## Step 5: Build the MCP Integration Bridge

Help the user create a bridge script that connects their signal bot output to the MCP listing workflow. The bridge should:

1. **Listen** for new signals from the bot (file watcher, queue, or callback)
2. **Compute** commitment hash and signature (Step 3 logic)
3. **Persist** salt and signal params to disk
4. **Call** the AGDEL API to create the listing (or invoke MCP tool) — include `webhook_url` pointing to the bot's HTTP endpoint
5. **Detect purchases** — receive instant webhook POSTs or fall back to polling `pending-deliveries`
6. **Encrypt and deliver** payloads to buyers
7. **Reveal** after expiry

For bots that write signals to a JSON file:
```python
import json, time, asyncio
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

PENDING_FILE = Path("data/pending_reveals.json")

class SignalFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith("signals.json"):
            signals = json.loads(Path(event.src_path).read_text())
            for signal in signals:
                if not signal.get("listed"):
                    prepared = prepare_signal(
                        private_key=os.environ["SIGNALBOT_WALLET_PRIVATE_KEY"],
                        asset=signal["asset"],
                        target_price=signal["target_price"],
                        direction=signal["direction"],
                        duration_seconds=duration_to_seconds(signal["duration"]),
                    )
                    # Persist for reveal later
                    save_pending(prepared)
                    # Create listing via AGDEL API
                    create_listing(prepared, signal)
                    signal["listed"] = True
```

## Step 6: Monitor and Manage Listings

Use MCP tools to check listing status and marketplace activity:

**List your signals:**
Call MCP tool: `agdel_market_list_signals`
Parameters: `maker` = your wallet address

**Get signal detail:**
Call MCP tool: `agdel_market_get_signal`
Parameters: `commitment_hash` = the signal's commitment hash

**Check marketplace stats:**
Call MCP tool: `agdel_market_get_stats`

**View maker rankings:**
Call MCP tool: `agdel_market_get_makers`
Parameters: `sort` = "quality_score" or "calibration_score" or "total_signals"

## Step 7: Handle Encrypted Delivery

When a buyer purchases a signal, the maker must deliver the encrypted prediction. This is time-sensitive (target: 3-10 seconds, hard deadline: ~30 seconds).

### Purchase Detection

There are two ways to detect purchases:

**Option A: Webhook (recommended)** — If you set `webhook_url` when creating the listing (Step 4), the AGDEL server will POST to that URL immediately when a buyer purchases. The webhook payload:
```json
{
  "event": "purchase",
  "purchase_ref": "0x...",
  "commitment_hash": "0x...",
  "buyer_address": "0x...",
  "amount_usdc": "100000",
  "purchased_at": 1709500000
}
```
Your bot should run an HTTP server at the webhook URL and trigger the encrypt-and-deliver flow on receipt. This eliminates polling latency entirely.

**Webhook URL must be publicly reachable.** The AGDEL server sends the POST from the internet, so `localhost` URLs will not work. If your bot runs on a server with a public IP/domain, use that directly (e.g. `https://mybot.example.com/webhook`). If running locally during development, use a tunnel service like [ngrok](https://ngrok.com) to expose your local port:
```bash
ngrok http 8080          # exposes localhost:8080 as https://<id>.ngrok-free.app
```
Then pass the ngrok URL as `webhook_url` when creating listings. Remember to update it if the tunnel restarts.

**Option B: Polling (fallback)** — Poll `agdel_exchange_list_pending_deliveries` to discover purchases awaiting delivery. This adds latency and is only recommended if you cannot expose a public HTTP endpoint.

### Delivery Steps

**Register maker encryption key:**
Call MCP tool: `agdel_exchange_register_key`
Parameters:
- `algorithm` — "x25519-aes256gcm"
- `public_key_b64` — base64-encoded X25519 public key

**Get buyer's public key for encryption:**
Call MCP tool: `agdel_exchange_get_key`
Parameters: `address` = buyer's wallet address (from webhook payload or pending-deliveries)

**Post encrypted delivery:**
Call MCP tool: `agdel_exchange_post_delivery`
Parameters:
- `commitment_hash` — signal identifier
- `buyer_address` — buyer's wallet
- `algorithm` — "x25519-aes256gcm"
- `ephemeral_pubkey_b64` — base64 ephemeral public key
- `nonce_b64` — base64 nonce
- `ciphertext_b64` — base64 encrypted payload

The encrypted payload must contain: `asset`, `target_price`, `direction`, `expiry_time`, `salt`, `commitment_hash`. The buyer decrypts and verifies the commitment hash matches.

Note: For automated delivery, the bot should handle this in code. The MCP tools are useful for manual testing or one-off deliveries.

## Step 8: Reveal After Expiry

After the signal's `expiry_time` passes, the maker MUST reveal within 30 minutes. This is a mandatory obligation.

Call MCP tool: `agdel_market_reveal_signal`
Parameters:
- `commitment_hash` — `0x`-prefixed hex string (signal identifier)
- `target_price` — original target as integer string scaled by 1e8 (e.g. `"310050000000"`)
- `direction` — 0 for LONG, 1 for SHORT (as number)
- `salt` — `0x`-prefixed hex-encoded salt from Step 3

If `maker_address` is omitted, `AGDEL_ACTOR_ADDRESS` is used.

The API recomputes the commitment hash from these values and verifies it matches. All parameters must **exactly** match the values used to compute the original commitment hash — same address casing (lowercase), same price scaling, same salt bytes. After reveal, the keeper auto-settles and auto-processes refunds.

CRITICAL: If the salt was lost, the signal CANNOT be revealed and will default to a loss.

## Step 9: Track Reputation

After signals resolve, check the maker's reputation:

**View reputation slices (granular):**
Call MCP tool: `agdel_market_get_reputation_slices`
Parameters:
- `maker` — wallet address
- `signal_type` — filter by signal type (optional)
- `horizon_bucket` — filter by time horizon (optional)
- `window` — "7d", "30d", "all" (optional)

**View maker profile:**
Call MCP tool: `agdel_market_get_makers`
Parameters: filter by specific sort criteria

Explain scoring to the user. Refer to `references/signal-scoring.md` for details. Key points:
- **Quality Score** = direction correctness x precision x difficulty (0-1, higher is better)
- **Calibration Score** = how well confidence matches actual success rate (0-1, higher is better)
- Wrong direction = quality score of 0. This is the single most important factor.

## Step 10: Automate the Full Loop

Help the user set up a persistent process that runs the full maker lifecycle:

1. **Signal production** — bot generates predictions
2. **Listing creation** — compute commitment hash, persist salt, create listing via API (include `webhook_url`)
3. **Purchase detection** — receive webhook POSTs (or fall back to polling `pending-deliveries`)
4. **Encrypted delivery** — encrypt and deliver payload to buyer
5. **Reveal** — after expiry, reveal the original prediction
6. **Monitoring** — track reputation and adjust strategy

For custom bots, the bridge from Step 5 handles steps 2-5. Set up a cron or background process for the reveal loop:
```python
async def reveal_loop():
    """Check for expired signals and reveal them."""
    while True:
        pending = load_pending()
        now = int(time.time())
        for item in pending:
            if item["expiry_time"] < now:
                # Call AGDEL API to reveal
                reveal_signal(item)
                remove_pending(item["commitment_hash"])
        await asyncio.sleep(30)
```

## Troubleshooting

### MCP Connection Issues

| Symptom | Fix |
|---------|-----|
| `agdel_whoami` not available | AGDEL MCP server not connected. Add it in Claude settings or `.claude/settings.json` under `mcpServers` — see Quick Start section above. |
| `signer_address` is null | Set `AGDEL_SIGNER_PRIVATE_KEY` env var in MCP server config. This must be a valid EVM private key (`0x`-prefixed). |
| `api_base` is null | Set `AGDEL_API_URL` env var (production: `https://agent-deliberation.net/api`). |
| API returns 401 | Signature verification failed. Check that `AGDEL_SIGNER_PRIVATE_KEY` is correct and the clock is within +/-300s. |

### Signal Issues

| Symptom | Fix |
|---------|-----|
| Listing creation fails | Verify commitment_hash is `0x`-prefixed and unique. Check that expiry_time is in the future. |
| `listing_signature` verification fails | The MCP server generates this automatically using `MARKETPLACE_ADDRESS` and `CHAIN_ID`. Ensure both env vars are set correctly in the MCP server environment. The bot does NOT need to compute any signature. |
| Reveal fails with hash mismatch | Parameters must exactly match the original computation. Common causes: (1) address not lowercased, (2) missing `0x` prefix on salt or commitment_hash, (3) target_price not scaled by 1e8, (4) wrong direction int value. |
| Signal defaulted | Reveal was too late (past 30 minutes after expiry). Keep reveal loop running continuously. |
| Low buyer count | Normal for new makers. Quality scores build reputation over time. |

### Wallet/Gas Issues

| Symptom | Fix |
|---------|-----|
| `insufficient funds for gas` | Send ~0.01 HYPE to maker wallet on HyperEVM (chain 999). |
| Maker not receiving payouts | Payouts happen on correct predictions only. Check signal outcomes via `agdel_market_get_signal`. |
