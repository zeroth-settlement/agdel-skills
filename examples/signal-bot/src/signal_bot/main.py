"""CLI entrypoint for the signal bot."""

from __future__ import annotations

import argparse
import asyncio
import os
import time

from dotenv import load_dotenv
from eth_account import Account

from .agdel import AgdelMCPClient, AgdelMCPError
from .config import load_config
from .crypto import load_or_create_encryption_keypair
from .publisher import PendingRevealStore, deliver_to_buyer, poll_and_deliver, publish_signal, reveal_expired
from .signal import fetch_candles, fetch_mark_price, generate_signal
from .webhook import WebhookServer, webhook_url_for


async def run(cfg: dict) -> None:
    """Main async loop: generate signals, publish, deliver, reveal."""
    signal_cfg = cfg.get("signal", {})
    agdel_cfg = cfg.get("agdel", {})

    coin = signal_cfg.get("coin", "ETH")
    interval = signal_cfg.get("interval_seconds", 60)
    candle_count = signal_cfg.get("candle_count", 5)
    candle_interval = signal_cfg.get("candle_interval", "1m")
    momentum_threshold = signal_cfg.get("momentum_threshold", 0.001)
    max_confidence = signal_cfg.get("max_confidence", 0.65)
    dry_run = agdel_cfg.get("dry_run", False)
    delivery_poll_seconds = agdel_cfg.get("delivery_poll_seconds", 10)
    reveal_poll_seconds = agdel_cfg.get("reveal_poll_seconds", 5)

    wallet_key = agdel_cfg.get("wallet_private_key") or os.environ.get("SIGNALBOT_WALLET_PRIVATE_KEY", "")
    if not wallet_key:
        print("[error] No wallet key. Set SIGNALBOT_WALLET_PRIVATE_KEY.", flush=True)
        return

    agdel_cfg["wallet_private_key"] = wallet_key
    account = Account.from_key(wallet_key)
    print(f"[bot] Wallet: {account.address}", flush=True)
    print(f"[bot] Coin: {coin} | Interval: {interval}s | Dry run: {dry_run}", flush=True)

    # Connect to AGDEL MCP
    mcp = AgdelMCPClient(
        wallet_private_key=wallet_key,
        api_url=agdel_cfg.get("api_url", "https://agent-deliberation.net/api"),
    )

    try:
        await mcp.start()
        info = await mcp.whoami()
        print(f"[bot] MCP connected — signer: {info.get('signer_address', 'unknown')}", flush=True)
    except Exception as exc:
        print(f"[bot] Failed to start MCP: {exc}", flush=True)
        return

    # Register encryption key
    keypair = load_or_create_encryption_keypair()
    try:
        await mcp.register_key(
            algorithm=keypair["algorithm"],
            public_key_b64=keypair["public_key_b64"],
        )
        print(f"[bot] Encryption key registered", flush=True)
    except AgdelMCPError as exc:
        print(f"[bot] Key registration warning: {exc}", flush=True)

    store = PendingRevealStore()

    # Delivery queue — fed by webhook POSTs, drained by the delivery task
    delivery_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)

    # Optional webhook server for instant purchase detection
    webhook_base_url = agdel_cfg.get("webhook_base_url") or os.environ.get("SIGNALBOT_WEBHOOK_BASE_URL", "")
    webhook_port = int(agdel_cfg.get("webhook_port", 0) or os.environ.get("SIGNALBOT_WEBHOOK_PORT", "8090"))
    webhook_server = None
    webhook_url = None

    if webhook_base_url:
        webhook_url = webhook_url_for(webhook_base_url)

        async def on_purchase(payload: dict) -> None:
            try:
                delivery_queue.put_nowait(payload)
            except asyncio.QueueFull:
                print("[webhook] Delivery queue full, dropping purchase", flush=True)

        webhook_server = WebhookServer(port=webhook_port, on_purchase=on_purchase)
        try:
            await webhook_server.start()
            print(f"[bot] Webhook URL: {webhook_url}", flush=True)
        except Exception as exc:
            print(f"[bot] Failed to start webhook server: {exc}", flush=True)
            webhook_server = None
            webhook_url = None
    else:
        print("[bot] No webhook URL configured — using polling for purchase detection", flush=True)

    print(f"[bot] Starting signal loop (every {interval}s)...", flush=True)

    # ── Concurrent tasks ─────────────────────────────────────────

    async def signal_loop() -> None:
        """Generate signals and publish to AGDEL."""
        while True:
            loop_start = time.time()
            try:
                candles = await fetch_candles(coin, candle_interval, candle_count)
                current_price = await fetch_mark_price(coin)

                if not candles or current_price <= 0:
                    print(f"[bot] No data for {coin}, retrying...", flush=True)
                    await asyncio.sleep(interval)
                    continue

                signal = generate_signal(
                    candles, current_price,
                    momentum_threshold=momentum_threshold,
                    max_confidence=max_confidence,
                )

                if signal:
                    print(
                        f"[signal] {coin} {signal['direction'].upper()} "
                        f"momentum={signal['momentum']:+.4f} "
                        f"conf={signal['confidence']:.3f} "
                        f"target=${signal['target_price']:.2f} "
                        f"price=${current_price:.2f}",
                        flush=True,
                    )
                    await publish_signal(mcp, account, store, keypair, signal, cfg, webhook_url=webhook_url)
                else:
                    print(
                        f"[signal] {coin} no signal (momentum below threshold) "
                        f"price=${current_price:.2f}",
                        flush=True,
                    )
            except AgdelMCPError as exc:
                print(f"[bot] MCP error: {exc}", flush=True)
            except Exception as exc:
                print(f"[bot] Signal loop error: {exc}", flush=True)

            elapsed = time.time() - loop_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def webhook_delivery_loop() -> None:
        """Drain the delivery queue (fed by webhook POSTs)."""
        print("[deliver] Webhook delivery loop started", flush=True)
        while True:
            try:
                purchase = await asyncio.wait_for(delivery_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            if dry_run:
                print(
                    f"[deliver-dry] Would deliver "
                    f"{purchase.get('commitment_hash', '?')[:18]}... -> "
                    f"{purchase.get('buyer_address', '?')[:10]}...",
                    flush=True,
                )
                continue

            commitment_hash = purchase.get("commitment_hash", "")
            buyer = purchase.get("buyer_address", "")
            if not commitment_hash or not buyer:
                continue

            try:
                await deliver_to_buyer(mcp, store, keypair, commitment_hash, buyer)
            except Exception as exc:
                print(
                    f"[deliver] Error {commitment_hash[:18]}... "
                    f"buyer={buyer[:10]}...: {exc}",
                    flush=True,
                )

    async def poll_delivery_loop() -> None:
        """Fallback: poll for purchases periodically."""
        if webhook_url:
            # Webhooks active — poll infrequently as safety net
            poll_interval = max(delivery_poll_seconds, 60)
        else:
            poll_interval = delivery_poll_seconds

        print(f"[deliver] Poll delivery loop started (interval={poll_interval}s)", flush=True)
        while True:
            await asyncio.sleep(poll_interval)
            if dry_run:
                continue
            try:
                await poll_and_deliver(mcp, store, keypair, dry_run=dry_run)
            except Exception as exc:
                print(f"[deliver] Poll error: {exc}", flush=True)

    async def reveal_loop() -> None:
        """Reveal expired signals."""
        while True:
            await asyncio.sleep(reveal_poll_seconds)
            try:
                await reveal_expired(mcp, store, dry_run=dry_run)
            except Exception as exc:
                print(f"[reveal] Loop error: {exc}", flush=True)

    # Run all tasks concurrently
    try:
        await asyncio.gather(
            signal_loop(),
            webhook_delivery_loop(),
            poll_delivery_loop(),
            reveal_loop(),
        )
    except KeyboardInterrupt:
        print("\n[bot] Shutting down...", flush=True)
    finally:
        if webhook_server:
            try:
                await webhook_server.stop()
            except Exception:
                pass
        try:
            await mcp.stop()
        except Exception:
            pass


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="AGDEL Signal Bot")
    parser.add_argument("--coin", default=None, help="Asset to trade (default: from config)")
    parser.add_argument("--config", default=None, help="Path to config YAML")
    parser.add_argument("--dry-run", action="store_true", help="Don't publish to AGDEL")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.coin:
        cfg["signal"]["coin"] = args.coin
    if args.dry_run:
        cfg["agdel"]["dry_run"] = True

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        print("\n[bot] Stopped.", flush=True)


if __name__ == "__main__":
    main()
