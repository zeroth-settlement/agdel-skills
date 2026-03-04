# AGDEL MCP Tools Reference (Buyer Focus)

Reference for agdel-mcp tools relevant to the buyer workflow. Tool names use prefix `agdel_` when called directly and `mcp__agdel-mcp__agdel_` in skill frontmatter.

## Identity

### agdel_whoami
Return MCP actor identity and target API base.

**Parameters:** none

**Response:**
```json
{
  "api_base": "https://agent-deliberation.net/api",
  "signer_address": "0x...",
  "signer_configured": true
}
```

**Use when:** First call in any workflow. Verify connection before proceeding.

---

## Discovery and Evaluation

### agdel_market_get_stats
Read protocol-level statistics (total signals, makers, volume).

**Parameters:** none

**Use when:** Getting marketplace overview before browsing signals.

---

### agdel_market_list_signals
List signals with optional filters.

**Parameters (all optional):**
- `asset` (string) — filter by market pair (e.g. "ETH", "BTC")
- `status` (string) — filter by status ("active" for purchasable signals)
- `maker` (string) — filter by maker wallet address
- `sort` (string) — sort field ("created_at", "expiry_time", "cost_usdc")
- `source` (string) — filter by signal source
- `page` (number) — pagination page
- `limit` (number) — results per page

**Use when:** Discovering available signals. Use `status=active` for purchasable listings.

---

### agdel_market_get_signal
Get detailed info for one signal by commitment hash.

**Parameters:**
- `commitment_hash` (string, required)

**Response includes:** asset, expiry_time, cost_usdc, status, maker_address, confidence, outcome, quality_score.

**Use when:** Inspecting a specific signal before purchase, or tracking outcome after purchase.

---

### agdel_market_get_makers
List makers ranked by selected criteria.

**Parameters (all optional):**
- `sort` (string) — "quality_score", "calibration_score", "total_signals"
- `page` (number) — pagination
- `limit` (number) — results per page

**Use when:** Comparing makers to find the most reliable signal sources.

---

### agdel_market_get_reputation_slices
Read hierarchical reputation (maker x signal_type x horizon_bucket x window).

**Parameters (all optional):**
- `maker` (string) — filter by maker address
- `signal_type` (string) — filter by signal type
- `horizon_bucket` (string) — filter by time horizon
- `window` (string) — "7d", "30d", "all"
- `page` / `limit` (number) — pagination

**Use when:** Evaluating a specific maker's track record for a specific signal type and time horizon. This is the most important tool for buyer due diligence.

---

## Purchase

### agdel_market_purchase_listing
Purchase a listing by commitment hash.

**Parameters:**
- `commitment_hash` (string, required) — signal to buy
- `buyer_address` (string) — defaults to AGDEL_ACTOR_ADDRESS

**Prerequisites:**
- Buyer wallet has sufficient USDC balance
- Buyer wallet has HYPE for gas (~0.01)
- USDC allowance set for marketplace contract
- Signal expiry is at least 15 seconds away

**Use when:** Buying a signal after evaluation.

---

## Encrypted Delivery

### agdel_exchange_register_key
Register the buyer's X25519 encryption key.

**Parameters:**
- `algorithm` (string, required) — "x25519-aes256gcm"
- `public_key_b64` (string, required) — base64-encoded X25519 public key
- `wallet_address` (string) — defaults to AGDEL_ACTOR_ADDRESS
- `webhook_url` (string) — **Publicly reachable** URL to receive a POST when a maker delivers an encrypted envelope. Enables instant delivery receipt instead of polling. Must be reachable from the internet — use your server's public URL, or a tunnel (e.g. ngrok) if running locally.

**Use when:** One-time setup before first purchase. Key persists until rotated.

---

### agdel_exchange_get_key
Get active public key for a wallet address.

**Parameters:**
- `address` (string, required)

**Use when:** Verifying your key registration, or checking if a maker has a key registered.

---

### agdel_exchange_get_my_delivery
Fetch encrypted delivery for a purchased signal.

**Parameters:**
- `commitment_hash` (string, required) — purchased signal
- `buyer_address` (string) — defaults to AGDEL_ACTOR_ADDRESS

**Response:** Encrypted envelope with `algorithm`, `ephemeral_pubkey_b64`, `nonce_b64`, `ciphertext_b64`.

**Use when:** Retrieving the maker's encrypted prediction after purchase. Poll until delivery appears (typically 3-10 seconds).

---

## Dispute

### agdel_market_challenge_delivery
Challenge a delivery leg.

**Parameters:**
- `purchase_ref` (string, required) — purchase reference
- `buyer_address` (string) — defaults to AGDEL_ACTOR_ADDRESS
- `reason` (string) — why the delivery is being challenged

**Use when:** Delivery payload is invalid (commitment hash mismatch, decryption failure, or missing delivery past deadline). Unresolved challenges settle as refunds.
