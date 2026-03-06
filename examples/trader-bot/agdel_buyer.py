"""AGDEL signal buyer — MCP-first integration with agent-deliberation.net.

Simplified from trader-bot-basic: no CxU, no complex scoring weights.
Core MCP buying + X25519 decryption + budget tracking.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("agdel_buyer")

ALG_ID = "x25519-aes256gcm"
HKDF_INFO = b"agdel-signal-delivery"

_HORIZON_RANGES: list[tuple[int, int, str]] = [
    (45, 90, "1m"),
    (240, 450, "5m"),
    (800, 1200, "15m"),
    (1500, 2400, "30m"),
    (3000, 5400, "1h"),
]


@dataclass
class BudgetTracker:
    max_per_signal: float
    max_hourly: float
    max_daily: float
    _hourly_spend: float = 0.0
    _daily_spend: float = 0.0
    _hourly_reset: float = field(default_factory=time.time)
    _daily_reset: float = field(default_factory=time.time)

    def can_spend(self, cost: float) -> tuple[bool, str]:
        now = time.time()
        if now - self._hourly_reset > 3600:
            self._hourly_spend = 0.0
            self._hourly_reset = now
        if now - self._daily_reset > 86400:
            self._daily_spend = 0.0
            self._daily_reset = now
        if cost > self.max_per_signal:
            return False, f"cost ${cost:.2f} > max ${self.max_per_signal:.2f}"
        if self._hourly_spend + cost > self.max_hourly:
            return False, f"hourly limit exceeded"
        if self._daily_spend + cost > self.max_daily:
            return False, f"daily limit exceeded"
        return True, ""

    def record(self, cost: float):
        self._hourly_spend += cost
        self._daily_spend += cost

    def status(self) -> dict:
        return {
            "hourlySpend": round(self._hourly_spend, 2),
            "hourlyLimit": self.max_hourly,
            "dailySpend": round(self._daily_spend, 2),
            "dailyLimit": self.max_daily,
        }


def _classify_horizon(duration_seconds: float) -> str | None:
    for min_s, max_s, label in _HORIZON_RANGES:
        if min_s <= duration_seconds <= max_s:
            return label
    return None


def decrypt_delivery(
    envelope: dict,
    buyer_private_key: X25519PrivateKey,
    commitment_hash: str,
    buyer_address: str,
    maker_address: str,
) -> dict:
    """Decrypt an AGDEL delivery envelope."""
    ephemeral_pub = X25519PublicKey.from_public_bytes(
        base64.b64decode(envelope["ephemeral_pubkey_b64"])
    )
    shared_secret = buyer_private_key.exchange(ephemeral_pub)
    derived_key = HKDF(
        algorithm=SHA256(), length=32,
        salt=None, info=HKDF_INFO,
    ).derive(shared_secret)
    nonce = base64.b64decode(envelope["nonce_b64"])
    ciphertext = base64.b64decode(envelope["ciphertext_b64"])
    plaintext = AESGCM(derived_key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)


class AgdelBuyer:
    """MCP-first AGDEL signal buyer."""

    def __init__(self, config: dict):
        ac = config.get("agdel", {})
        self.enabled: bool = ac.get("enabled", True)
        self.auto_buy: bool = ac.get("autoBuy", False)
        self.poll_interval: int = ac.get("pollIntervalSeconds", 30)
        self.api_url: str = ac.get("apiUrl", "https://agent-deliberation.net/api")
        self.assets: list[str] = ac.get("assets", ["ETH"])
        self.fetch_limit: int = ac.get("selection", {}).get("fetchLimit", 100)

        budget = ac.get("budget", {})
        self.budget = BudgetTracker(
            max_per_signal=budget.get("maxCostPerSignalUsdc", 2.0),
            max_hourly=budget.get("maxHourlySpendUsdc", 10.0),
            max_daily=budget.get("maxDailySpendUsdc", 50.0),
        )

        sel = ac.get("selection", {})
        self.min_confidence: float = sel.get("minSignalConfidence", 0.2)
        self.target_horizons: dict[str, int] = sel.get("targetHorizons", {"5m": 1, "15m": 1})

        mf = ac.get("makerFilters", {})
        self.min_maker_win_rate: float = mf.get("minWinRate", 0.3)
        self.min_maker_signals: int = mf.get("minTotalSignals", 5)

        exc = ac.get("exchange", {})
        self.delivery_poll_seconds: int = exc.get("deliveryPollSeconds", 1)
        self.delivery_timeout: int = exc.get("deliveryTimeoutSeconds", 60)
        self.key_file_path: str = exc.get("keyFilePath", "data/buyer_exchange_key.bin")

        webhook_base = os.environ.get("TRADERBOT_WEBHOOK_BASE_URL", "")
        if webhook_base:
            self.webhook_url = webhook_base.rstrip("/") + "/api/agdel/webhook/delivery"
        else:
            self.webhook_url = exc.get("webhookUrl", "")

        self._rpc_url: str = os.environ.get("AGDEL_RPC_URL", "https://rpc.hyperliquid.xyz/evm")
        self._usdc_address: str = os.environ.get(
            "AGDEL_USDC_ADDRESS", "0xb88339cb7199b77e23db6e890353e22632ba630f"
        )
        self._usdc_balance: float = 0.0

        self.signals: dict[str, dict] = {}
        self.purchased_hashes: set[str] = set()
        self.purchase_log: deque[dict] = deque(maxlen=200)
        self.available_signals: list[dict] = []
        self._mcp_session: Any = None
        self._mcp_context: Any = None
        self._buyer_private_key: X25519PrivateKey | None = None
        self._buyer_public_key_b64: str = ""
        self._buyer_address: str = ""
        self._pending_deliveries: dict[str, dict] = {}
        self._maker_cache: dict[str, dict] = {}
        self._stats = {
            "polls": 0, "purchases": 0, "deliveries": 0,
            "errors": 0, "lastPollAt": None,
        }

    async def start(self):
        if not self.enabled:
            logger.info("AGDEL buyer disabled")
            return

        self._buyer_address = os.environ.get("TRADERBOT_WALLET_ADDRESS", "")
        if not self._buyer_address:
            self._buyer_address = self._derive_address_from_key()
        if not self._buyer_address:
            self._buyer_address = os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")

        logger.info("AGDEL buyer address: %s", self._buyer_address)
        self._load_or_generate_keypair()
        await self._refresh_usdc_balance()
        await self._connect_mcp()

        if self._mcp_session:
            try:
                register_args = {
                    "algorithm": ALG_ID,
                    "public_key_b64": self._buyer_public_key_b64,
                }
                if self.webhook_url:
                    register_args["webhook_url"] = self.webhook_url
                await self._call_tool("agdel_exchange_register_key", register_args)
                logger.info("Registered buyer encryption key")
            except Exception as e:
                logger.warning("Failed to register encryption key: %s", e)

    async def stop(self):
        if self._mcp_session:
            try:
                await self._mcp_session.__aexit__(None, None, None)
            except Exception:
                pass
        if self._mcp_context:
            try:
                await self._mcp_context.__aexit__(None, None, None)
            except Exception:
                pass

    async def _connect_mcp(self):
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client

            server_path = os.environ.get("AGDEL_MCP_PATH", "")

            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "NODE_PATH": os.environ.get("NODE_PATH", ""),
                "MCP_HEALTH_PORT": "0",
                "AGDEL_API_URL": self.api_url,
                "AGDEL_SIGNER_PRIVATE_KEY": (
                    os.environ.get("AGDEL_PRIVATE_KEY")
                    or os.environ.get("TRADERBOT_WALLET_PRIVATE_KEY", "")
                ),
                "MARKETPLACE_ADDRESS": os.environ.get(
                    "AGDEL_MARKETPLACE_ADDRESS", "0x1779255c0AcDe950095C9E872B2fAD06CFB88D4c"
                ),
                "AGDEL_RPC_URL": os.environ.get(
                    "AGDEL_RPC_URL", "https://rpc.hyperliquid.xyz/evm"
                ),
                "AGDEL_ONCHAIN": "1",
            }

            if server_path:
                # Local dev: run from agdel monorepo root
                params = StdioServerParameters(
                    command="npx",
                    args=["tsx", "mcp/server.ts"],
                    cwd=server_path,
                    env=env,
                )
            else:
                # Published package
                params = StdioServerParameters(
                    command="npx",
                    args=["-y", "agdel-mcp"],
                    env=env,
                )

            self._mcp_context = stdio_client(params)
            read_stream, write_stream = await self._mcp_context.__aenter__()
            self._mcp_session = ClientSession(read_stream, write_stream)
            await self._mcp_session.__aenter__()
            await self._mcp_session.initialize()
            logger.info("MCP session initialized")

        except ImportError:
            logger.error("mcp package not installed. Run: pip install mcp")
        except Exception as e:
            logger.error("Failed to connect MCP server: %s", e)
            self._mcp_session = None

    async def _call_tool(self, tool_name: str, arguments: dict | None = None) -> Any:
        if not self._mcp_session:
            raise RuntimeError("MCP session not connected")
        result = await self._mcp_session.call_tool(tool_name, arguments or {})
        if getattr(result, "isError", False):
            texts = []
            if hasattr(result, "content") and result.content:
                for block in result.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
            raise RuntimeError(f"{tool_name}: {' | '.join(texts)}")
        if hasattr(result, "content") and result.content:
            for block in result.content:
                if hasattr(block, "text"):
                    try:
                        return json.loads(block.text)
                    except json.JSONDecodeError:
                        return block.text
        return result

    async def poll_once(self) -> list[dict]:
        if not self._mcp_session:
            return []
        self._stats["polls"] += 1
        self._stats["lastPollAt"] = time.time()
        purchased = []
        await self._refresh_usdc_balance()

        try:
            for asset in self.assets:
                signals = await self._call_tool("agdel_market_list_signals", {
                    "asset": asset, "status": "available", "limit": self.fetch_limit,
                })
                if not isinstance(signals, list):
                    if isinstance(signals, dict):
                        signals = signals.get("items", signals.get("signals", []))
                    else:
                        signals = []
                self.available_signals = signals

                if self.auto_buy:
                    candidates = self._filter_candidates(signals)
                    for candidate in candidates:
                        result = await self._purchase_and_receive(candidate)
                        if result:
                            purchased.append(result)
        except Exception as e:
            self._stats["errors"] += 1
            logger.error("AGDEL poll error: %s", e)
        return purchased

    def _filter_candidates(self, signals: list[dict]) -> list[dict]:
        """Filter signals by basic criteria."""
        candidates = []
        now = time.time()
        for sig in signals:
            commitment_hash = sig.get("commitment_hash", "")
            if commitment_hash in self.purchased_hashes:
                continue
            expiry = sig.get("expiry_time", 0)
            if isinstance(expiry, str):
                try:
                    expiry = int(expiry)
                except ValueError:
                    continue
            if expiry - now < 30:
                continue
            horizon = sig.get("horizon_bucket") or _classify_horizon(expiry - now)
            if horizon not in self.target_horizons:
                continue
            confidence = float(sig.get("confidence", 0) or 0)
            if confidence < self.min_confidence:
                continue
            raw_cost = float(sig.get("cost_usdc", 0) or 0)
            cost = raw_cost / 1_000_000 if raw_cost > 100 else raw_cost
            can_afford, _ = self.budget.can_spend(cost)
            if not can_afford:
                continue
            rep = sig.get("maker_track_record", {})
            win_rate = float(rep.get("hit_rate", rep.get("win_rate", 0)) or 0)
            if win_rate < self.min_maker_win_rate:
                continue
            candidates.append({
                **sig, "horizon": horizon, "cost": cost,
                "maker": sig.get("maker_address", sig.get("maker", "")),
                "confidence": confidence,
            })
        return candidates

    async def _purchase_and_receive(self, candidate: dict) -> dict | None:
        commitment_hash = candidate.get("commitment_hash", "")
        cost = candidate.get("cost", 0)
        try:
            result = await self._call_tool("agdel_market_purchase_listing", {
                "commitment_hash": commitment_hash,
            })
            if isinstance(result, dict) and not result.get("purchase_ref"):
                return None
            self.purchased_hashes.add(commitment_hash)
            self.budget.record(cost)
            self._stats["purchases"] += 1
            logger.info("Purchased %s", commitment_hash[:12])

            if self.webhook_url:
                self._pending_deliveries[commitment_hash] = {
                    "candidate": candidate, "purchased_at": time.time(),
                    "maker": candidate.get("maker", ""),
                }
                self.purchase_log.appendleft({
                    "commitment_hash": commitment_hash,
                    "horizon": candidate.get("horizon"),
                    "cost": cost, "purchased_at": time.time(), "delivered": False,
                })
                return None
            else:
                payload = await self._poll_delivery(commitment_hash, candidate.get("maker", ""))
                if payload:
                    signal = self._convert_signal(payload, candidate)
                    self.signals[candidate["horizon"]] = signal
                    entry = {
                        "commitment_hash": commitment_hash,
                        "horizon": candidate.get("horizon"),
                        "cost": cost, "purchased_at": time.time(),
                        "delivered": True,
                    }
                    self.purchase_log.appendleft(entry)
                    self._update_purchase_log(commitment_hash, payload)
                    return signal
                self.purchase_log.appendleft({
                    "commitment_hash": commitment_hash,
                    "horizon": candidate.get("horizon"),
                    "cost": cost, "purchased_at": time.time(), "delivered": False,
                })
                return None
        except Exception as e:
            self._stats["errors"] += 1
            logger.error("Purchase failed for %s: %s", commitment_hash[:12], e)
            self.purchase_log.appendleft({
                "commitment_hash": commitment_hash,
                "horizon": candidate.get("horizon"),
                "cost": cost, "purchased_at": time.time(),
                "delivered": False, "error": str(e),
            })
            return None

    async def _poll_delivery(self, commitment_hash: str, maker_address: str) -> dict | None:
        deadline = time.time() + self.delivery_timeout
        while time.time() < deadline:
            try:
                delivery = await self._call_tool("agdel_exchange_get_my_delivery", {
                    "commitment_hash": commitment_hash,
                })
            except Exception:
                await asyncio.sleep(self.delivery_poll_seconds)
                continue
            if delivery and isinstance(delivery, dict) and delivery.get("ciphertext_b64"):
                if not self._buyer_private_key:
                    return None
                try:
                    payload = decrypt_delivery(
                        delivery, self._buyer_private_key,
                        commitment_hash, self._buyer_address, maker_address,
                    )
                    self._stats["deliveries"] += 1
                    return payload
                except Exception as e:
                    logger.error("Decryption failed: %s", e)
                    return None
            await asyncio.sleep(self.delivery_poll_seconds)
        return None

    def _convert_signal(self, payload: dict, meta: dict) -> dict:
        direction = payload.get("direction", 0)
        if isinstance(direction, str):
            direction = 1 if direction.lower() in ("long", "1") else -1
        confidence = meta.get("confidence", 0.5)
        score = confidence * (1 if direction == 1 else -1)
        return {
            "source": "agdel", "score": score, "confidence": confidence,
            "horizon": meta.get("horizon", "5m"),
            "direction": "long" if direction == 1 else "short",
            "target_price": payload.get("target_price"),
            "maker": meta.get("maker", ""),
            "commitment_hash": payload.get("commitment_hash", ""),
            "cost_usdc": meta.get("cost", 0),
            "received_at": time.time(),
            "expiry_time": payload.get("expiry_time", 0),
        }

    async def manual_purchase(self, commitment_hash: str) -> dict:
        """Purchase a specific signal by commitment hash (from dashboard button)."""
        if not self._mcp_session:
            return {"ok": False, "error": "MCP not connected"}
        sig = None
        for s in self.available_signals:
            if s.get("commitment_hash") == commitment_hash:
                sig = s
                break
        if not sig:
            return {"ok": False, "error": "Signal not found"}

        now = time.time()
        expiry = sig.get("expiry_time", 0)
        if isinstance(expiry, str):
            try:
                expiry = int(expiry)
            except ValueError:
                expiry = 0
        duration = expiry - now if expiry else 0
        horizon = sig.get("horizon_bucket") or _classify_horizon(duration) or "unknown"
        maker = sig.get("maker_address", sig.get("maker", ""))
        raw_cost = float(sig.get("cost_usdc", 0) or 0)
        cost = raw_cost / 1_000_000 if raw_cost > 100 else raw_cost

        rep = sig.get("maker_track_record", {})
        confidence = float(sig.get("confidence", 0) or 0)
        calibration = float(rep.get("calibration_score", 0) or 0)
        candidate = {
            "commitment_hash": commitment_hash, "horizon": horizon,
            "cost": cost, "maker": maker,
            "confidence": confidence,
            "calibration": calibration,
            "conf_calib": round(confidence * calibration, 4),
            "signal_type": sig.get("signal_type", ""),
            "quality_score": float(rep.get("avg_quality_score", rep.get("quality_score", 0)) or 0),
            "entry_price": sig.get("entry_price"),
            "created_at": sig.get("created_at"),
        }

        try:
            result = await self._call_tool("agdel_market_purchase_listing", {
                "commitment_hash": commitment_hash,
            })
            if isinstance(result, dict) and not result.get("purchase_ref"):
                error = result.get("error", str(result))
                self.purchase_log.appendleft({
                    "commitment_hash": commitment_hash, "horizon": horizon,
                    "cost": cost, "purchased_at": time.time(),
                    "delivered": False, "error": str(error),
                })
                return {"ok": False, "error": str(error)}

            self.purchased_hashes.add(commitment_hash)
            self.budget.record(cost)
            self._stats["purchases"] += 1
            self.purchase_log.appendleft({
                "commitment_hash": commitment_hash, "horizon": horizon,
                "cost": cost, "purchased_at": time.time(), "delivered": False,
                "maker": maker[:12] if maker else "",
                "confidence": candidate["confidence"],
                "calibration": candidate["calibration"],
                "conf_calib": candidate["conf_calib"],
                "signal_type": candidate["signal_type"],
                "quality_score": candidate["quality_score"],
                "entry_price": candidate.get("entry_price"),
                "created_at": candidate.get("created_at"),
                "expiry_time": expiry,
            })

            if self.webhook_url:
                self._pending_deliveries[commitment_hash] = {
                    "candidate": candidate, "purchased_at": time.time(),
                    "maker": maker,
                }
            else:
                asyncio.create_task(self._background_receive(candidate))

            return {"ok": True, "horizon": horizon, "cost": cost}
        except Exception as e:
            self._stats["errors"] += 1
            return {"ok": False, "error": str(e)}

    async def _background_receive(self, candidate: dict):
        commitment_hash = candidate.get("commitment_hash", "")
        maker = candidate.get("maker", "")
        try:
            payload = await self._poll_delivery(commitment_hash, maker)
            if payload:
                signal = self._convert_signal(payload, candidate)
                self.signals[candidate.get("horizon", "5m")] = signal
                self._update_purchase_log(commitment_hash, payload)
        except Exception as e:
            logger.error("Background delivery error: %s", e)

    async def handle_webhook_delivery(self, payload: dict) -> dict | None:
        commitment_hash = payload.get("commitment_hash", "")
        logger.info("Webhook delivery for %s (pending keys: %s)",
                    commitment_hash[:12],
                    [k[:12] for k in self._pending_deliveries.keys()])
        pending = self._pending_deliveries.pop(commitment_hash, None)
        if not pending:
            logger.warning("No pending delivery for %s", commitment_hash[:12])
            return None
        candidate = pending["candidate"]
        envelope = {
            "ephemeral_pubkey_b64": payload.get("ephemeral_pubkey_b64"),
            "nonce_b64": payload.get("nonce_b64"),
            "ciphertext_b64": payload.get("ciphertext_b64"),
        }
        if not envelope.get("ciphertext_b64") or not self._buyer_private_key:
            return None
        try:
            decrypted = decrypt_delivery(
                envelope, self._buyer_private_key,
                commitment_hash, self._buyer_address,
                payload.get("maker_address", candidate.get("maker", "")),
            )
            signal = self._convert_signal(decrypted, candidate)
            self.signals[candidate.get("horizon", "5m")] = signal
            self._stats["deliveries"] += 1
            self._update_purchase_log(commitment_hash, decrypted)
            return signal
        except Exception as e:
            logger.error("Webhook decryption failed: %s", e)
            return None

    async def check_stale_deliveries(self):
        """Fall back to polling for any pending webhook deliveries older than 30s."""
        stale = []
        now = time.time()
        for ch, info in list(self._pending_deliveries.items()):
            if now - info["purchased_at"] > 30:
                stale.append((ch, info))
        for ch, info in stale:
            try:
                payload = await self._poll_delivery(ch, info["maker"])
                if payload:
                    candidate = info["candidate"]
                    signal = self._convert_signal(payload, candidate)
                    self.signals[candidate.get("horizon", "5m")] = signal
                    self._pending_deliveries.pop(ch, None)
                    self._update_purchase_log(ch, payload)
            except Exception:
                pass

    async def check_outcomes(self):
        """Poll AGDEL for resolution outcomes on recent purchases."""
        if not self._mcp_session:
            return
        now = time.time()
        for entry in self.purchase_log:
            if entry.get("outcome"):
                continue
            if not entry.get("delivered"):
                continue
            expiry = entry.get("expiry_time", 0)
            if not expiry or now < expiry:
                continue
            # Only check signals expired more than 30s ago (give keeper time)
            if now - expiry < 30:
                continue
            try:
                sig = await self._call_tool("agdel_market_get_signal", {
                    "commitment_hash": entry["commitment_hash"],
                })
                if isinstance(sig, dict):
                    status = sig.get("status", "")
                    if status in ("resolved", "settled"):
                        qs = sig.get("quality_score")
                        entry["outcome"] = "HIT" if qs and float(qs) > 0 else "MISS"
                        entry["quality_score"] = qs
                        entry["resolution_price"] = sig.get("resolution_price")
                        logger.info("Resolution %s: %s (quality=%s)",
                                    entry["commitment_hash"][:10], entry["outcome"], qs)
                    elif status == "defaulted":
                        entry["outcome"] = "DEFAULT"
                        logger.info("Resolution %s: DEFAULT", entry["commitment_hash"][:10])
            except Exception as e:
                logger.debug("Outcome check failed for %s: %s", entry["commitment_hash"][:10], e)

    def handle_webhook_resolution(self, body: dict) -> dict | None:
        """Handle a resolution webhook event — instant outcome update."""
        commitment_hash = body.get("commitment_hash", "")
        if not commitment_hash:
            return None
        for entry in self.purchase_log:
            if entry.get("commitment_hash") == commitment_hash:
                if entry.get("outcome"):
                    return entry
                status = body.get("status", "")
                if status in ("resolved", "settled"):
                    qs = body.get("quality_score")
                    entry["outcome"] = "HIT" if qs and float(qs) > 0 else "MISS"
                    entry["quality_score"] = qs
                    entry["resolution_price"] = body.get("resolution_price")
                    logger.info("Webhook resolution %s: %s (quality=%s)",
                                commitment_hash[:10], entry["outcome"], qs)
                elif status == "defaulted":
                    entry["outcome"] = "DEFAULT"
                    logger.info("Webhook resolution %s: DEFAULT", commitment_hash[:10])
                return entry
        return None

    def get_latest_signals(self) -> dict[str, dict | None]:
        now = time.time()
        result = {}
        for hz in self.target_horizons:
            sig = self.signals.get(hz)
            if sig:
                expiry = sig.get("expiry_time", 0)
                if expiry and now > expiry:
                    sig = None
                elif now - sig.get("received_at", 0) > 960:
                    sig = None
            result[hz] = sig
        return result

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "budget": self.budget.status(),
            "purchasedCount": len(self.purchased_hashes),
        }

    def get_wallet_info(self) -> dict:
        addr = self._buyer_address
        return {
            "address": addr,
            "addressShort": (addr[:8] + "..." + addr[-6:]) if len(addr) > 14 else addr,
            "usdcBalance": round(self._usdc_balance, 6),
        }

    def get_available_enriched(self) -> list[dict]:
        now = time.time()
        enriched = []
        for sig in self.available_signals:
            expiry = sig.get("expiry_time", 0)
            if isinstance(expiry, str):
                try:
                    expiry = int(expiry)
                except (ValueError, TypeError):
                    continue
            if expiry <= now:
                continue
            duration = expiry - now
            horizon = sig.get("horizon_bucket") or _classify_horizon(duration)
            maker = sig.get("maker_address", sig.get("maker", ""))
            rep = sig.get("maker_track_record") or self._maker_cache.get(maker, {})
            confidence = float(sig.get("confidence", 0) or 0)
            calibration = float(rep.get("calibration_score", 0) or 0)
            win_rate = float(rep.get("hit_rate", rep.get("win_rate", 0)) or 0)
            quality = float(rep.get("avg_quality_score", rep.get("quality_score", 0)) or 0)
            raw_cost = float(sig.get("cost_usdc", 0) or 0)
            cost = raw_cost / 1_000_000 if raw_cost > 100 else raw_cost
            enriched.append({
                "commitmentHash": sig.get("commitment_hash", ""),
                "commitmentHashShort": sig.get("commitment_hash", "")[:12],
                "horizon": horizon or f"{int(duration)}s",
                "maker": maker[:12] if maker else "",
                "cost": cost, "confidence": confidence,
                "signalType": sig.get("signal_type", ""),
                "quality": quality, "winRate": win_rate,
                "calibration": calibration,
                "confCalib": round(confidence * calibration, 4),
                "totalSignals": int(rep.get("total_signals", 0) or 0),
                "expiresIn": int(duration),
            })
        enriched.sort(key=lambda s: s["confCalib"], reverse=True)
        return enriched

    def _update_purchase_log(self, commitment_hash: str, payload: dict):
        """Update purchase log entry with decrypted delivery fields.

        Only overwrites existing values if the payload value is not None,
        so API-sourced fields like expiry_time aren't clobbered.
        """
        for entry in self.purchase_log:
            if entry.get("commitment_hash") == commitment_hash:
                entry["delivered"] = True
                for key in ("direction", "target_price", "expiry_time", "salt", "asset"):
                    val = payload.get(key)
                    if val is not None:
                        entry[key] = val
                break

    async def get_signal_detail(self, commitment_hash: str) -> dict:
        """Fetch full signal detail from AGDEL API and merge with local purchase data."""
        result = {"local": None, "agdel": None}
        for entry in self.purchase_log:
            if entry.get("commitment_hash") == commitment_hash:
                result["local"] = dict(entry)
                break
        if self._mcp_session:
            try:
                sig = await self._call_tool("agdel_market_get_signal", {
                    "commitment_hash": commitment_hash,
                })
                if isinstance(sig, str):
                    sig = json.loads(sig)
                result["agdel"] = sig
            except Exception as e:
                result["agdel_error"] = str(e)
        return result

    def _derive_address_from_key(self) -> str:
        pk = os.environ.get("TRADERBOT_WALLET_PRIVATE_KEY", "") or os.environ.get("AGDEL_PRIVATE_KEY", "")
        if not pk:
            return ""
        try:
            from eth_account import Account
            return Account.from_key(pk).address
        except Exception:
            return ""

    async def _refresh_usdc_balance(self):
        if not self._buyer_address:
            return
        padded = self._buyer_address.lower().replace("0x", "").zfill(64)
        data = "0x70a08231" + padded
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self._rpc_url, json={
                    "jsonrpc": "2.0", "method": "eth_call",
                    "params": [{"to": self._usdc_address, "data": data}, "latest"],
                    "id": 1,
                })
                raw = int(resp.json().get("result", "0x0"), 16)
                self._usdc_balance = raw / 1_000_000
        except Exception:
            pass

    def _load_or_generate_keypair(self):
        key_path = Path(self.key_file_path)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            self._buyer_private_key = X25519PrivateKey.from_private_bytes(key_path.read_bytes())
        else:
            self._buyer_private_key = X25519PrivateKey.generate()
            key_path.write_bytes(self._buyer_private_key.private_bytes_raw())
        pub_bytes = self._buyer_private_key.public_key().public_bytes_raw()
        self._buyer_public_key_b64 = base64.b64encode(pub_bytes).decode()
