"""Hyperliquid execution — unified paper + live trading interface."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("hl_trader")

HL_API_URL = "https://api.hyperliquid.xyz"
TAKER_FEE_PCT = 0.00035  # 0.035% per side


@dataclass
class Position:
    size: float = 0.0         # positive = long, negative = short, 0 = flat
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: int = 1
    paper: bool = True

    @property
    def side(self) -> str:
        if self.size > 0:
            return "long"
        elif self.size < 0:
            return "short"
        return "flat"

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "side": self.side,
            "entryPrice": self.entry_price,
            "unrealizedPnl": round(self.unrealized_pnl, 4),
            "leverage": self.leverage,
            "paper": self.paper,
        }


@dataclass
class TradeResult:
    success: bool
    action: str
    size: float
    price: float
    fee: float = 0.0
    pnl: float = 0.0
    error: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "action": self.action,
            "size": round(self.size, 6),
            "price": round(self.price, 2),
            "fee": round(self.fee, 4),
            "pnl": round(self.pnl, 4),
            "error": self.error,
            "timestamp": self.timestamp,
        }


class HLTrader:
    """Unified trading interface for Hyperliquid perpetuals."""

    def __init__(self, config: dict, mode: str = "paper"):
        self.mode = mode
        tc = config.get("trading", {})
        self.asset: str = tc.get("assets", ["ETH"])[0]
        self.max_leverage: int = tc.get("maxLeverage", 15)
        self.risk_per_trade: float = tc.get("riskPerTrade", 0.0075)

        # Paper state
        self._paper_balance: float = tc.get("paperStartingBalanceUsd", 1000.0)
        self._paper_starting_balance: float = self._paper_balance
        self._paper_position: Position | None = None
        self._paper_trades: list[dict] = []

        # Live state
        self._http = httpx.AsyncClient(timeout=15, base_url=HL_API_URL)
        self._exchange: Any = None  # hyperliquid Exchange instance
        self._info: Any = None      # hyperliquid Info instance
        self._main_address: str = ""
        self._asset_index: int | None = None
        self._sz_decimals: int = 4

    async def connect(self):
        """Initialize connections. For live mode, set up hyperliquid SDK."""
        self._main_address = os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")

        if self.mode == "live":
            try:
                from hyperliquid.exchange import Exchange
                from hyperliquid.info import Info
                from hyperliquid.utils import constants
                from eth_account import Account

                private_key = os.environ.get("TRADERBOT_WALLET_PRIVATE_KEY", "")
                wallet = Account.from_key(private_key)

                self._info = Info(constants.MAINNET_API_URL)
                self._exchange = Exchange(wallet, constants.MAINNET_API_URL)

                # Fetch asset metadata for index and size decimals
                meta = self._info.meta()
                for i, asset_info in enumerate(meta.get("universe", [])):
                    if asset_info["name"] == self.asset:
                        self._asset_index = i
                        self._sz_decimals = asset_info.get("szDecimals", 4)
                        break

                logger.info("Live trading connected: wallet=%s asset=%s idx=%s",
                            wallet.address, self.asset, self._asset_index)
            except Exception as e:
                logger.error("Failed to init live trading: %s", e)
                raise
        else:
            logger.info("Paper trading mode: starting balance=$%.2f", self._paper_balance)

    async def get_hl_account(self) -> tuple[Position | None, dict]:
        """Always fetch position + portfolio from Hyperliquid API (single call)."""
        empty_portfolio = {"equity": 0, "availableBalance": 0, "pnl": 0, "paper": False}
        if not self._main_address:
            return None, empty_portfolio
        try:
            resp = await self._http.post("/info", json={
                "type": "clearinghouseState",
                "user": self._main_address,
            })
            resp.raise_for_status()
            state = resp.json()

            # Position
            position = None
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                if p.get("coin") == self.asset:
                    size = float(p.get("szi", 0))
                    entry = float(p.get("entryPx", 0))
                    upnl = float(p.get("unrealizedPnl", 0))
                    lev = int(float(p.get("leverage", {}).get("value", 1)))
                    position = Position(size=size, entry_price=entry,
                                        unrealized_pnl=upnl, leverage=lev, paper=False)
                    break

            # Portfolio
            margin = state.get("marginSummary", {})
            equity = float(margin.get("accountValue", 0))
            available = float(margin.get("totalRawUsd", 0))
            portfolio = {
                "equity": round(equity, 2),
                "availableBalance": round(available, 2),
                "pnl": 0.0,
                "paper": False,
            }

            return position, portfolio
        except Exception as e:
            logger.error("Failed to get HL account: %s", e)
            return None, empty_portfolio

    async def get_mark_price(self) -> float:
        """Fetch current mid price from Hyperliquid."""
        try:
            resp = await self._http.post("/info", json={"type": "allMids"})
            resp.raise_for_status()
            mids = resp.json()
            price_str = mids.get(self.asset, "0")
            return float(price_str)
        except Exception as e:
            logger.error("Failed to fetch mark price: %s", e)
            return 0.0

    async def get_position(self) -> Position | None:
        """Get current position (paper or live)."""
        if self.mode == "paper":
            return self._paper_position

        # Live mode
        try:
            resp = await self._http.post("/info", json={
                "type": "clearinghouseState",
                "user": self._main_address,
            })
            resp.raise_for_status()
            state = resp.json()
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                if p.get("coin") == self.asset:
                    size = float(p.get("szi", 0))
                    entry = float(p.get("entryPx", 0))
                    upnl = float(p.get("unrealizedPnl", 0))
                    lev = int(float(p.get("leverage", {}).get("value", 1)))
                    return Position(size=size, entry_price=entry,
                                    unrealized_pnl=upnl, leverage=lev, paper=False)
            return None
        except Exception as e:
            logger.error("Failed to get position: %s", e)
            return None

    async def get_portfolio(self) -> dict:
        """Get account equity and balance."""
        if self.mode == "paper":
            upnl = 0.0
            if self._paper_position:
                upnl = self._paper_position.unrealized_pnl
            equity = self._paper_balance + upnl
            return {
                "equity": round(equity, 2),
                "availableBalance": round(self._paper_balance, 2),
                "pnl": round(equity - self._paper_starting_balance, 2),
                "paper": True,
            }

        try:
            resp = await self._http.post("/info", json={
                "type": "clearinghouseState",
                "user": self._main_address,
            })
            resp.raise_for_status()
            state = resp.json()
            margin = state.get("marginSummary", {})
            equity = float(margin.get("accountValue", 0))
            available = float(margin.get("totalRawUsd", 0))
            return {
                "equity": round(equity, 2),
                "availableBalance": round(available, 2),
                "pnl": 0.0,
                "paper": False,
            }
        except Exception as e:
            logger.error("Failed to get portfolio: %s", e)
            return {"equity": 0, "availableBalance": 0, "pnl": 0, "paper": self.mode == "paper"}

    async def execute(self, action: str, size_pct: float, mark_price: float) -> TradeResult | None:
        """Execute a trade action. Returns None if action is hold."""
        if action == "hold":
            return None
        # Close/flip actions don't need a size — they operate on the existing position
        if size_pct <= 0 and action not in ("close", "flip_long", "flip_short"):
            return None

        if self.mode == "paper":
            return await self._execute_paper(action, size_pct, mark_price)
        else:
            return await self._execute_live(action, size_pct, mark_price)

    async def _execute_paper(self, action: str, size_pct: float, mark_price: float) -> TradeResult:
        """Simulate a trade in paper mode."""
        pos = self._paper_position
        portfolio = await self.get_portfolio()
        equity = portfolio["equity"]

        # Compute size in asset units
        notional = equity * self.risk_per_trade * self.max_leverage * size_pct
        size = notional / mark_price if mark_price > 0 else 0
        fee = notional * TAKER_FEE_PCT
        pnl = 0.0

        if action in ("open_long", "open_short"):
            # Close any existing position first
            if pos and pos.size != 0:
                pnl = pos.unrealized_pnl
                self._paper_balance += pnl - fee
            direction = 1 if action == "open_long" else -1
            self._paper_position = Position(
                size=size * direction, entry_price=mark_price,
                leverage=self.max_leverage, paper=True,
            )

        elif action == "increase" and pos:
            old_notional = abs(pos.size) * pos.entry_price
            new_notional = abs(size) * mark_price
            total_size = abs(pos.size) + size
            avg_entry = (old_notional + new_notional) / total_size if total_size > 0 else mark_price
            direction = 1 if pos.size > 0 else -1
            self._paper_position = Position(
                size=total_size * direction, entry_price=avg_entry,
                leverage=self.max_leverage, paper=True,
            )

        elif action in ("decrease_long", "decrease_short") and pos:
            reduce_size = min(abs(size), abs(pos.size) * self.risk_per_trade)
            reduce_fraction = reduce_size / abs(pos.size) if pos.size != 0 else 0
            pnl = pos.unrealized_pnl * reduce_fraction
            remaining = abs(pos.size) - reduce_size
            direction = 1 if pos.size > 0 else -1
            if remaining > 0.0001:
                self._paper_position = Position(
                    size=remaining * direction, entry_price=pos.entry_price,
                    leverage=self.max_leverage, paper=True,
                )
            else:
                self._paper_position = None
            self._paper_balance += pnl - fee

        elif action == "close" and pos:
            pnl = pos.unrealized_pnl
            self._paper_balance += pnl - fee
            self._paper_position = None

        elif action in ("flip_long", "flip_short") and pos:
            pnl = pos.unrealized_pnl
            self._paper_balance += pnl - fee
            direction = 1 if action == "flip_long" else -1
            self._paper_position = Position(
                size=size * direction, entry_price=mark_price,
                leverage=self.max_leverage, paper=True,
            )
            fee *= 2  # double fee for close + open

        else:
            return TradeResult(success=False, action=action, size=0, price=mark_price,
                               error=f"Invalid action '{action}' for current position")

        # Update unrealized PnL for new position
        if self._paper_position and self._paper_position.size != 0:
            price_diff = mark_price - self._paper_position.entry_price
            self._paper_position.unrealized_pnl = (
                price_diff * self._paper_position.size
            )

        result = TradeResult(
            success=True, action=action, size=size,
            price=mark_price, fee=fee, pnl=pnl,
        )
        self._paper_trades.append(result.to_dict())
        logger.info("Paper trade: %s size=%.4f price=%.2f pnl=%.4f",
                     action, size, mark_price, pnl)
        return result

    async def _execute_live(self, action: str, size_pct: float, mark_price: float) -> TradeResult:
        """Execute a real trade via Hyperliquid SDK."""
        if not self._exchange:
            return TradeResult(success=False, action=action, size=0,
                               price=mark_price, error="Exchange not initialized")

        portfolio = await self.get_portfolio()
        equity = portfolio["equity"]
        notional = equity * self.risk_per_trade * self.max_leverage * size_pct
        size = round(notional / mark_price, self._sz_decimals) if mark_price > 0 else 0

        try:
            if action in ("open_long", "flip_long"):
                # Close existing position first if flipping
                if action == "flip_long":
                    pos = await self.get_position()
                    if pos and pos.size < 0:
                        self._exchange.market_close(self.asset)
                result = self._exchange.market_open(self.asset, True, size)

            elif action in ("open_short", "flip_short"):
                if action == "flip_short":
                    pos = await self.get_position()
                    if pos and pos.size > 0:
                        self._exchange.market_close(self.asset)
                result = self._exchange.market_open(self.asset, False, size)

            elif action == "increase":
                pos = await self.get_position()
                is_buy = pos.size > 0 if pos else True
                result = self._exchange.market_open(self.asset, is_buy, size)

            elif action in ("decrease_long", "decrease_short"):
                pos = await self.get_position()
                if pos:
                    reduce_size = min(size, abs(pos.size) * 0.5)
                    reduce_size = round(reduce_size, self._sz_decimals)
                    is_buy = pos.size < 0  # buy to reduce short, sell to reduce long
                    result = self._exchange.market_open(self.asset, is_buy, reduce_size, reduce_only=True)
                else:
                    return TradeResult(success=False, action=action, size=0,
                                       price=mark_price, error="No position to reduce")

            elif action == "close":
                result = self._exchange.market_close(self.asset)

            else:
                return TradeResult(success=False, action=action, size=0,
                                   price=mark_price, error=f"Unknown action: {action}")

            # Parse SDK result
            if result is None:
                logger.error("Live trade returned None for %s", action)
                return TradeResult(success=False, action=action, size=size,
                                   price=mark_price, error="SDK returned None")
            logger.info("Live trade raw result for %s: %s", action, str(result)[:200])
            if isinstance(result, dict):
                status = result.get("status", "")
            else:
                status = str(result)
            if status == "ok":
                fee = notional * TAKER_FEE_PCT
                logger.info("Live trade: %s size=%.4f price=%.2f", action, size, mark_price)
                return TradeResult(success=True, action=action, size=size,
                                   price=mark_price, fee=fee)
            else:
                err = result.get("response", str(result)) if isinstance(result, dict) else str(result)
                logger.error("Live trade failed: %s", err)
                return TradeResult(success=False, action=action, size=size,
                                   price=mark_price, error=str(err))

        except Exception as e:
            logger.error("Live trade exception: %s", e)
            return TradeResult(success=False, action=action, size=size,
                               price=mark_price, error=str(e))

    def set_mode(self, mode: str):
        """Switch between paper and live trading."""
        if mode not in ("paper", "live"):
            raise ValueError(f"Invalid mode: {mode}")
        old = self.mode
        self.mode = mode
        if mode == "paper" and old == "live":
            logger.info("Switched to PAPER mode")
        elif mode == "live" and old == "paper":
            logger.warning("Switched to LIVE mode — real orders will be placed")

    def reset_paper(self, starting_balance: float | None = None):
        """Reset paper trading state."""
        if starting_balance is not None:
            self._paper_starting_balance = starting_balance
        self._paper_balance = self._paper_starting_balance
        self._paper_position = None
        self._paper_trades.clear()

    def update_paper_pnl(self, mark_price: float):
        """Update unrealized PnL for paper position."""
        if self._paper_position and self._paper_position.size != 0:
            price_diff = mark_price - self._paper_position.entry_price
            self._paper_position.unrealized_pnl = price_diff * self._paper_position.size
