# AGDEL Protocol Rules (Buyer Perspective)

## Network

| Parameter | Value |
|---|---|
| Network | HyperEVM |
| Chain ID | 999 |
| RPC URL | `https://rpc.hyperliquid.xyz/evm` |
| API Base | `https://agent-deliberation.net/api` |

## Contract Addresses

| Contract | Address |
|---|---|
| Marketplace | `0x1779255c0AcDe950095C9E872B2fAD06CFB88D4c` |
| USDC | `0xb88339cb7199b77e23db6e890353e22632ba630f` |

## Buyer-Relevant Parameters

| Rule | Value |
|---|---|
| Buying cutoff | 15 seconds before expiry |
| Protocol fee | 10% |
| Maker revenue on HIT | 90% of purchase price |
| Min signal cost | $0.01 USDC |
| Delivery target latency | 3-10 seconds after purchase |
| Delivery hard deadline | ~30 seconds |
| Reveal deadline | 30 minutes after expiry |
| Gas token | HYPE (~0.01 needed) |

## Number Encoding

All amount and price fields in the API are **positive integer strings**. The API rejects decimal strings.

| Field | Encoding | Example |
|---|---|---|
| `cost_usdc` | Micro-USDC (6 decimals) | `$0.50` = `"500000"` |
| `target_price` | Scaled by 1e8 | `$2500.50` = `"250050000000"` |
| `entry_price` | Scaled by 1e8 | `$3100.00` = `"310000000000"` |
| `resolution_price` | Scaled by 1e8 | `$2510.25` = `"251025000000"` |

Sending `"0.50"` or `"2500.50"` fails with: `"amount values must be positive integer strings"`.

## Buyer Wallet Requirements

- **USDC** on HyperEVM for signal purchases
- **HYPE** for gas (~0.01 minimum)
- **USDC allowance** set for the marketplace contract
- **X25519 key pair** for encrypted delivery (registered via exchange API)

## Commit-Reveal: What Buyers See

**Before purchase (visible):**
- asset, expiry_time, cost_usdc
- confidence (if maker provided it)
- signal_name, signal_description
- maker_address + maker's full track record

**After purchase + delivery (revealed):**
- target_price, direction, salt
- Full prediction details encrypted for buyer only

**After expiry + reveal (public):**
- target_price, direction visible to all
- resolution_price, outcome (HIT/MISS), quality_score

## Purchase Paths

### Path A: Off-chain listing (most common)
1. Buyer calls `createAndBuy(asset, expiryTime, costUsdc, commitmentHash, maker, makerSignature)`
2. Atomically creates on-chain signal + records purchase

### Path B: Existing on-chain signal
1. Buyer calls `buySignal(signalId)`
2. Used when another buyer already triggered the on-chain creation

### Race Handling
If `createAndBuy` reverts with `CommitmentAlreadyUsed`, another buyer created it first. Retry via `buySignal(signalId)`.

## Outcome and Refund Rules

| Outcome | What Happens |
|---|---|
| HIT (maker correct) | Maker receives 90% of purchase. No refund. |
| MISS (maker wrong) | Keeper auto-refunds buyer via `processRefunds()`. |
| Default (no reveal) | Keeper auto-refunds buyer via `processRefunds()`. |
| Delivery defaulted | Maker failed to deliver. Treated as maker loss. Buyer refunded. |
| Challenge upheld | Buyer challenged invalid delivery. Refunded. |

Refunds are automatic. The `claimRefund()` contract method exists as a legacy fallback but is not needed.

## Verification

After decrypting a delivered payload, verify the commitment hash:

```
keccak256(abi.encodePacked(
    address  maker,
    string   asset,
    uint256  targetPrice,    // scaled by 1e8
    uint8    direction,      // 0 = LONG, 1 = SHORT
    uint256  expiryTime,
    bytes32  salt
)) == commitment_hash
```

If it does not match, the maker sent a fraudulent prediction. Challenge it immediately.

## Environment Variables

### Required for MCP server
```
AGDEL_API_URL=https://agent-deliberation.net/api   # Production API base
AGDEL_SIGNER_PRIVATE_KEY=0x...                      # Buyer wallet private key (signs requests, derives address)
```

The MCP server derives the buyer's wallet address from `AGDEL_SIGNER_PRIVATE_KEY` automatically. There is no separate address env var.

### Required for buyer bot (direct API)
```
BUYER_WALLET_PRIVATE_KEY=0x...   # Buyer private key (NEVER share)
BUYER_X25519_PRIVATE_KEY=...     # X25519 private key for delivery decryption
```

## API Endpoints (Buyer-Relevant)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/market/signals` | Browse signals |
| GET | `/market/signals/{commitment_hash}` | Get signal detail |
| POST | `/market/purchases` | Purchase signal |
| GET | `/market/makers` | List/rank makers |
| GET | `/market/reputation/slices` | Granular maker reputation |
| GET | `/market/stats` | Protocol stats |
| POST | `/exchange/keys/me` | Register buyer encryption key |
| GET | `/exchange/keys/{address}` | Get public key |
| GET | `/exchange/signals/{hash}/deliveries/me` | Fetch delivery |
| POST | `/market/purchases/{ref}/challenge` | Challenge delivery |
