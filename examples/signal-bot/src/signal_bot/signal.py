"""Momentum signal generation from Hyperliquid candle data."""

from __future__ import annotations

import httpx

HL_API_URL = "https://api.hyperliquid.xyz"


async def fetch_candles(
    coin: str,
    interval: str = "1m",
    count: int = 5,
) -> list[dict]:
    """Fetch recent candles from Hyperliquid REST API.

    Returns list of {open, high, low, close, volume, time} dicts.
    """
    # Hyperliquid candleSnapshot endpoint
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{HL_API_URL}/info",
            json={
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": 0,  # API returns latest when startTime=0
                },
            },
        )
        resp.raise_for_status()
        raw = resp.json()

    # Take last N candles
    candles = []
    for c in raw[-count:]:
        candles.append({
            "time": c["t"],
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
        })
    return candles


async def fetch_mark_price(coin: str) -> float:
    """Fetch current mid price from Hyperliquid."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{HL_API_URL}/info",
            json={"type": "allMids"},
        )
        resp.raise_for_status()
        mids = resp.json()
    return float(mids.get(coin, "0"))


def compute_momentum(candles: list[dict]) -> float:
    """Compute simple momentum = (close[-1] - close[0]) / close[0]."""
    if len(candles) < 2:
        return 0.0
    first_close = candles[0]["close"]
    last_close = candles[-1]["close"]
    if first_close <= 0:
        return 0.0
    return (last_close - first_close) / first_close


def generate_signal(
    candles: list[dict],
    current_price: float,
    momentum_threshold: float = 0.001,
    max_confidence: float = 0.65,
) -> dict | None:
    """Generate a directional signal from candle momentum.

    Returns None if momentum is below threshold (no signal).
    Returns dict with direction, target_price, confidence, entry_price.
    """
    momentum = compute_momentum(candles)

    if abs(momentum) < momentum_threshold:
        return None

    direction = "long" if momentum > 0 else "short"
    confidence = min(abs(momentum) * 100, max_confidence)
    # Target: half-move continuation from current price
    target_price = round(current_price * (1 + momentum * 0.5), 2)

    return {
        "direction": direction,
        "target_price": target_price,
        "entry_price": current_price,
        "confidence": round(confidence, 4),
        "momentum": round(momentum, 6),
    }
