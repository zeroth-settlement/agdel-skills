"""AGDEL signal publisher — publish, deliver, and reveal lifecycle.

Simplified from market-fragility-bot: no CxU, no per-horizon cooldowns,
no webhook delivery queue. Core publish/deliver/reveal loop only.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .agdel import AgdelMCPClient, AgdelMCPError
from .crypto import (
    confidence_to_cost,
    encrypt_for_buyer,
    load_or_create_encryption_keypair,
    prepare_signal,
)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_STATE_FILE = _DATA_DIR / "pending_reveals.json"


class PendingRevealStore:
    """JSON-backed store for pending signal reveals."""

    def __init__(self, state_file: str | Path | None = None) -> None:
        self._state_path = Path(state_file) if state_file else _STATE_FILE
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._pending = data.get("pending", [])
            except Exception as exc:
                print(f"[store] Failed to load state: {exc}")

    def _save(self) -> None:
        tmp = self._state_path.with_suffix(".json.tmp")
        payload = {
            "pending": self._pending,
            "updated_at": int(time.time()),
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    def add(self, item: dict) -> None:
        self._pending.append(item)
        self._save()

    def find_by_commitment_hash(self, commitment_hash: str) -> dict | None:
        for item in self._pending:
            if item.get("commitment_hash") == commitment_hash:
                return item
        return None

    def mark_delivered(self, commitment_hash: str, buyer: str) -> None:
        for item in self._pending:
            if item.get("commitment_hash") == commitment_hash:
                delivered = item.setdefault("delivered_to", [])
                if buyer not in delivered:
                    delivered.append(buyer)
                    self._save()
                return

    def get_revealable(self) -> list[dict]:
        now = int(time.time())
        return [item for item in self._pending if item["expiry_time"] < now]

    def get_active_hashes(self) -> list[str]:
        now = int(time.time())
        return [
            item["commitment_hash"]
            for item in self._pending
            if item["expiry_time"] >= now
        ]

    def remove(self, commitment_hash: str) -> None:
        self._pending = [
            item for item in self._pending
            if item.get("commitment_hash") != commitment_hash
        ]
        self._save()

    @property
    def pending_count(self) -> int:
        return len(self._pending)


async def publish_signal(
    mcp: AgdelMCPClient,
    account: Any,
    store: PendingRevealStore,
    keypair: dict,
    prediction: dict,
    cfg: dict,
    webhook_url: str | None = None,
) -> bool:
    """Publish a single signal prediction to AGDEL.

    Returns True on success, False on failure.
    """
    acfg = cfg.get("agdel", {})
    scfg = cfg.get("signal", {})
    coin = scfg.get("coin", "ETH")
    horizon = scfg.get("horizon", "5m")
    horizon_seconds = scfg.get("horizon_seconds", 300)

    conf = prediction["confidence"]
    cost = confidence_to_cost(
        conf,
        acfg.get("cost_usdc_min", 0.05),
        acfg.get("cost_usdc_max", 0.20),
    )

    try:
        prepared = prepare_signal(
            private_key=acfg["wallet_private_key"],
            asset=coin,
            target_price=prediction["target_price"],
            direction=prediction["direction"],
            duration_seconds=horizon_seconds,
        )

        cost_usdc_scaled = int(round(cost * 10**6))

        if acfg.get("dry_run"):
            print(
                f"[publish-dry] Would publish {coin} {horizon} "
                f"dir={prediction['direction']} conf={conf:.3f} cost=${cost:.2f} "
                f"hash={prepared['commitment_hash'][:18]}...",
                flush=True,
            )
        else:
            await mcp.create_listing(
                commitment_hash=prepared["commitment_hash"],
                asset=coin,
                expiry_time=prepared["expiry_time"],
                cost_usdc=str(cost_usdc_scaled),
                signal_type=acfg.get("signal_type", "price_prediction"),
                maker_address=prepared["maker"],
                signal_name=acfg.get("signal_name", "momentum-signal"),
                signal_description=acfg.get("signal_description", ""),
                confidence=conf,
                entry_price=str(int(round(prediction["entry_price"] * 10**8))),
                horizon_bucket=horizon,
                webhook_url=webhook_url,
            )

        # Persist for reveal
        store.add({
            "commitment_hash": prepared["commitment_hash"],
            "salt_hex": prepared["salt_hex"],
            "target_price_scaled": prepared["target_price_scaled"],
            "direction_int": prepared["direction_int"],
            "entry_price": prediction["entry_price"],
            "target_price": prediction["target_price"],
            "confidence": conf,
            "expiry_time": prepared["expiry_time"],
            "coin": coin,
            "horizon": horizon,
            "direction": prediction["direction"],
            "cost_usdc": cost,
            "created_at": int(time.time()),
        })

        if not acfg.get("dry_run"):
            print(
                f"[published] {coin} {horizon} {prediction['direction']} "
                f"target=${prediction['target_price']:.2f} conf={conf:.3f} "
                f"cost=${cost:.2f} pending={store.pending_count}",
                flush=True,
            )
        return True

    except Exception as exc:
        print(f"[publish] Error: {exc}", flush=True)
        return False


async def deliver_to_buyer(
    mcp: AgdelMCPClient,
    store: PendingRevealStore,
    keypair: dict,
    commitment_hash: str,
    buyer: str,
) -> bool:
    """Encrypt and deliver signal payload to a single buyer."""
    item = store.find_by_commitment_hash(commitment_hash)
    if not item:
        print(f"[deliver] No pending signal for {commitment_hash[:18]}...", flush=True)
        return False

    delivered_to = set(item.get("delivered_to", []))
    if buyer in delivered_to:
        return True

    key_info = await mcp.get_key(buyer)
    buyer_pubkey = key_info.get("public_key_b64", "")
    if not buyer_pubkey:
        print(f"[deliver] No pubkey for buyer {buyer[:10]}...", flush=True)
        return False

    plaintext = json.dumps({
        "target_price": item["target_price"],
        "direction": item["direction"],
        "confidence": item.get("confidence", 0),
        "entry_price": item.get("entry_price", 0),
        "coin": item["coin"],
        "horizon": item["horizon"],
        "salt": item["salt_hex"],
    }).encode()

    envelope = encrypt_for_buyer(plaintext, buyer_pubkey)

    await mcp.post_delivery(
        commitment_hash=commitment_hash,
        buyer_address=buyer,
        algorithm=keypair["algorithm"],
        ephemeral_pubkey_b64=envelope["ephemeral_pubkey_b64"],
        nonce_b64=envelope["nonce_b64"],
        ciphertext_b64=envelope["ciphertext_b64"],
    )

    store.mark_delivered(commitment_hash, buyer)
    print(
        f"[delivered] {commitment_hash[:18]}... -> {buyer[:10]}... "
        f"{item['coin']} {item['horizon']}",
        flush=True,
    )
    return True


async def poll_and_deliver(
    mcp: AgdelMCPClient,
    store: PendingRevealStore,
    keypair: dict,
    dry_run: bool = False,
) -> None:
    """Check for purchases and deliver to buyers."""
    if dry_run:
        return

    active_hashes = store.get_active_hashes()
    if not active_hashes:
        return

    for commitment_hash in active_hashes:
        try:
            signal_detail = await mcp.get_signal(commitment_hash)
        except AgdelMCPError:
            continue

        purchases = signal_detail.get("purchases", [])
        if not purchases:
            continue

        item = store.find_by_commitment_hash(commitment_hash)
        if not item:
            continue

        delivered_to = set(item.get("delivered_to", []))
        for purchase in purchases:
            buyer = purchase.get("buyer_address", "")
            if not buyer or buyer in delivered_to:
                continue
            try:
                await deliver_to_buyer(mcp, store, keypair, commitment_hash, buyer)
            except Exception as exc:
                print(
                    f"[deliver] Error {commitment_hash[:18]}... "
                    f"buyer={buyer[:10]}...: {exc}",
                    flush=True,
                )


async def reveal_expired(
    mcp: AgdelMCPClient,
    store: PendingRevealStore,
    dry_run: bool = False,
) -> None:
    """Reveal all expired signals."""

    revealable = store.get_revealable()
    if not revealable:
        return

    now = int(time.time())
    for item in revealable:
        commitment_hash = item.get("commitment_hash")
        if not commitment_hash:
            store.remove(commitment_hash or "")
            continue

        # Expire after 24h (unrevealed too long)
        if now - item["expiry_time"] > 86400:
            print(f"[reveal] EXPIRED: {commitment_hash[:18]}... past 24h, removing", flush=True)
            store.remove(commitment_hash)
            continue

        salt_hex = item["salt_hex"]
        if not salt_hex.startswith("0x"):
            salt_hex = f"0x{salt_hex}"

        try:
            if dry_run:
                print(
                    f"[reveal-dry] Would reveal {commitment_hash[:18]}... "
                    f"{item['coin']} {item['horizon']} "
                    f"dir={item['direction_int']} target={item['target_price_scaled']}",
                    flush=True,
                )
                store.remove(commitment_hash)
            else:
                await mcp.reveal_signal(
                    commitment_hash=commitment_hash,
                    target_price=str(item["target_price_scaled"]),
                    direction=item["direction_int"],
                    salt=salt_hex,
                )
                print(
                    f"[revealed] {commitment_hash[:18]}... "
                    f"{item['coin']} {item['horizon']}",
                    flush=True,
                )
                store.remove(commitment_hash)
        except AgdelMCPError as exc:
            if "not found" in str(exc).lower():
                print(f"[reveal] {commitment_hash[:18]}... not found, removing", flush=True)
                store.remove(commitment_hash)
            else:
                print(f"[reveal] Failed for {commitment_hash[:18]}...: {exc}", flush=True)
        except Exception as exc:
            print(f"[reveal] Failed for {commitment_hash[:18]}...: {exc}", flush=True)
