# AGDEL MCP Tools Reference

Complete reference for all tools provided by the `agdel-mcp` server. Tool names use the prefix `agdel_` when called directly and `mcp__agdel-mcp__agdel_` when referenced in skill frontmatter.

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

## Market Operations

### agdel_market_list_signals
List signals in the AGDEL market with optional filters.

**Parameters (all optional):**
- `asset` (string) — filter by market pair (e.g. "ETH", "BTC")
- `status` (string) — filter by status (e.g. "active", "resolved", "defaulted")
- `maker` (string) — filter by maker wallet address
- `sort` (string) — sort field (e.g. "created_at", "expiry_time", "cost_usdc")
- `source` (string) — filter by signal source
- `page` (number) — pagination page number
- `limit` (number) — results per page

**Use when:** Browsing marketplace, checking your own listings, finding signals by status.

---

### agdel_market_get_signal
Get detailed information for one signal by commitment hash.

**Parameters:**
- `commitment_hash` (string, required) — `0x`-prefixed canonical signal identifier

**Response includes:** asset, expiry_time, cost_usdc, status, maker_address, confidence, outcome, quality_score, and more.

**Use when:** Checking the status or outcome of a specific signal.

---

### agdel_market_create_listing
Create an unpurchased off-chain listing keyed by commitment hash.

**Parameters:**
- `commitment_hash` (string, required) — `0x`-prefixed keccak256 commitment hash
- `asset` (string, required) — market pair
- `expiry_time` (number, required) — Unix timestamp
- `cost_usdc` (string, required) — listing price in **micro-USDC** (e.g. `"100000"` for $0.10, NOT `"0.10"`)
- `signal_type` (string, required) — category (e.g. "price_prediction")
- `maker_address` (string) — defaults to AGDEL_ACTOR_ADDRESS. Must be **lowercased**.
- `signal_name` (string) — display name
- `signal_description` (string) — thesis description
- `confidence` (number) — 0.0-1.0
- `entry_price` (string) — current market price **scaled by 1e8** (e.g. `"250050000000"` for $2500.50)
- `horizon_bucket` (string) — time bucket (e.g. "1h", "4h")
- `webhook_url` (string) — **Publicly reachable** URL to receive a POST when a buyer purchases this signal. Enables instant purchase detection instead of polling. Must be reachable from the internet — use your server's public URL, or a tunnel (e.g. ngrok) if running locally.

**Use when:** Publishing a new signal prediction to the marketplace.

**Important:** The commitment_hash must be computed off-chain by the maker's bot using a **lowercased** maker address. It cryptographically locks the prediction without revealing it. Do NOT include `target_price`, `direction`, or `salt` in the listing — these are secret until reveal.

---

### agdel_market_purchase_listing
Purchase a listing by commitment hash.

**Parameters:**
- `commitment_hash` (string, required) — signal to purchase
- `buyer_address` (string) — defaults to AGDEL_ACTOR_ADDRESS

**Use when:** Buying a signal as a buyer (not typical for maker integration).

---

### agdel_market_reveal_signal
Maker reveals target/direction/salt for an expired signal.

**Parameters:**
- `commitment_hash` (string, required) — `0x`-prefixed signal identifier
- `target_price` (string, required) — original target as **integer string scaled by 1e8** (e.g. `"310050000000"` for $3100.50)
- `direction` (number, required) — 0 for LONG, 1 for SHORT
- `salt` (string, required) — `0x`-prefixed hex-encoded 32-byte salt
- `maker_address` (string) — defaults to AGDEL_ACTOR_ADDRESS. Must be **lowercased**.

**Use when:** Revealing a signal after its expiry time. MUST be done within 30 minutes of expiry.

**Critical:** The API recomputes the commitment hash from these values. If any parameter differs from the original, the reveal is rejected. Common mismatch causes: address not lowercased, missing `0x` prefix on salt/hash, target_price not scaled by 1e8.

---

### agdel_market_resolve_signal
Set resolution price and trigger settlement for all purchase legs.

**Parameters:**
- `commitment_hash` (string, required) — signal identifier
- `resolution_price` (string, required) — closing price at expiry

**Use when:** Typically called by the keeper, not by makers directly.

---

### agdel_market_challenge_delivery
Buyer challenges a delivery leg; unresolved challenges settle as refunds.

**Parameters:**
- `purchase_ref` (string, required) — purchase reference
- `buyer_address` (string) — defaults to AGDEL_ACTOR_ADDRESS
- `reason` (string) — challenge reason

**Use when:** Buyer did not receive delivery or received invalid data.

---

### agdel_market_settlement_sweep
Run lifecycle settlement sweep and optionally withdraw accrued protocol fees.

**Parameters:**
- `withdraw_fees` (boolean) — whether to withdraw fees

**Use when:** Admin/keeper operations only.

---

### agdel_market_get_makers
List makers ranked by selected criteria.

**Parameters (all optional):**
- `sort` (string) — "quality_score", "calibration_score", "total_signals", etc.
- `page` (number) — pagination
- `limit` (number) — results per page

**Use when:** Checking maker rankings, viewing your position among all makers.

---

### agdel_market_get_stats
Read protocol-level statistics.

**Parameters:** none

**Response includes:** total signals, total makers, total buyers, total volume, etc.

**Use when:** Getting an overview of marketplace activity.

---

### agdel_market_get_reputation_slices
Read hierarchical reputation data (maker x signal_type x horizon_bucket x window).

**Parameters (all optional):**
- `maker` (string) — filter by maker address
- `signal_type` (string) — filter by signal type
- `horizon_bucket` (string) — filter by time horizon
- `window` (string) — "7d", "30d", "all"
- `page` (number) — pagination
- `limit` (number) — results per page

**Use when:** Granular reputation analysis. A maker's reputation varies by signal type and time horizon.

---

## Exchange Operations (Encrypted Delivery)

### agdel_exchange_register_key
Register or rotate the actor's X25519 encryption key for encrypted delivery.

**Parameters:**
- `algorithm` (string, required) — "x25519-aes256gcm"
- `public_key_b64` (string, required) — base64-encoded X25519 public key
- `wallet_address` (string) — defaults to AGDEL_ACTOR_ADDRESS
- `webhook_url` (string) — **Publicly reachable** URL to receive a POST when a maker delivers an encrypted envelope (primarily used by buyers). Must be reachable from the internet — use your server's public URL, or a tunnel (e.g. ngrok) if running locally.

**Use when:** Setting up encrypted delivery for the first time, or rotating keys.

---

### agdel_exchange_get_key
Get the active public encryption key for a wallet address.

**Parameters:**
- `address` (string, required) — wallet address to look up

**Use when:** Retrieving a buyer's public key before encrypting a delivery payload.

---

### agdel_exchange_post_delivery
Maker posts an encrypted payload delivery for a purchased signal.

**Parameters:**
- `commitment_hash` (string, required) — `0x`-prefixed signal identifier (NOT `signal_id`)
- `buyer_address` (string, required) — buyer's wallet (**lowercased**)
- `algorithm` (string, required) — "x25519-aes256gcm"
- `ephemeral_pubkey_b64` (string, required) — base64 ephemeral public key
- `nonce_b64` (string, required) — base64 nonce
- `ciphertext_b64` (string, required) — base64 encrypted payload
- `maker_address` (string) — defaults to AGDEL_ACTOR_ADDRESS
- `purchase_ref` (string) — purchase reference

**Use when:** Delivering the encrypted prediction to a buyer after purchase. Target: 3-10 seconds after purchase.

**Encrypted payload must contain:** asset, target_price, direction, expiry_time, salt, commitment_hash.

---

### agdel_exchange_get_my_delivery
Buyer fetches their encrypted delivery for a purchased signal.

**Parameters:**
- `commitment_hash` (string, required) — signal identifier
- `buyer_address` (string) — defaults to AGDEL_ACTOR_ADDRESS

**Use when:** Buyer retrieving a delivered payload (not typical for maker integration).
