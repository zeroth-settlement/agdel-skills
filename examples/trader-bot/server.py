"""FastAPI server — orchestrates AGDEL buying, matrix decisions, and trading.

Simplified from trader-bot-basic: no reflection engine, added approve/reject endpoints.
The key feature is human-in-the-loop: the dashboard shows recommendations,
the user clicks Approve or Reject.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from agdel_buyer import AgdelBuyer
from hl_trader import HLTrader
from matrix_engine import MatrixEngine

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("server")

CONFIG_PATH = Path("config/trading.yaml")
config: dict = {}
trading_mode: str = "paper"

matrix_engine: MatrixEngine | None = None
agdel_buyer: AgdelBuyer | None = None
hl_trader: HLTrader | None = None

connected_clients: set[WebSocket] = set()
tick_history: deque[dict] = deque(maxlen=500)
trade_history: deque[dict] = deque(maxlen=200)
latest_state: dict = {}

# Pending approval: stores the latest matrix decision awaiting user action
pending_approval: dict | None = None

_tick_task: asyncio.Task | None = None
_agdel_task: asyncio.Task | None = None


def load_config() -> dict:
    global config
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f) or {}
    return config


# ── Background loops ─────────────────────────────────────────────────

async def tick_loop():
    interval = config.get("trading", {}).get("loopIntervalMs", 5000) / 1000
    await asyncio.sleep(2)
    while True:
        try:
            await _run_tick()
        except Exception as e:
            logger.error("Tick error: %s", e)
        await asyncio.sleep(interval)


async def _run_tick():
    global latest_state, pending_approval

    if not hl_trader:
        return

    now = time.time()
    mark_price = await hl_trader.get_mark_price()
    if mark_price <= 0:
        return

    if trading_mode == "paper":
        hl_trader.update_paper_pnl(mark_price)

    position = await hl_trader.get_position()
    pos_dict = position.to_dict() if position else {"size": 0, "side": "flat"}

    signals = agdel_buyer.get_latest_signals() if agdel_buyer else {}
    fast_hz = config.get("matrix", {}).get("signalHorizons", {}).get("fast", "5m")
    slow_hz = config.get("matrix", {}).get("signalHorizons", {}).get("slow", "15m")
    fast_signal = signals.get(fast_hz)
    slow_signal = signals.get(slow_hz)

    decision = matrix_engine.decide(fast_signal, slow_signal, pos_dict) if matrix_engine else None

    # Store pending approval if action is not hold (DO NOT auto-execute)
    if decision and decision.action != "hold":
        pending_approval = {
            "action": decision.action,
            "size_pct": decision.size_pct,
            "mark_price": mark_price,
            "fast_state": decision.fast_state,
            "slow_state": decision.slow_state,
            "reason": decision.reason,
            "timestamp": now,
        }

    portfolio = await hl_trader.get_portfolio()
    hl_position, hl_portfolio = await hl_trader.get_hl_account()
    hl_pos_dict = hl_position.to_dict() if hl_position else {
        "size": 0, "side": "flat", "entryPrice": 0,
        "unrealizedPnl": 0, "leverage": 1, "paper": False,
    }

    state = {
        "timestamp": now,
        "tradingMode": trading_mode,
        "asset": config.get("trading", {}).get("assets", ["ETH"])[0],
        "markPrice": mark_price,
        "position": pos_dict,
        "hlPosition": hl_pos_dict,
        "hlPortfolio": hl_portfolio,
        "signals": {
            "fast": _signal_summary(fast_signal, fast_hz),
            "slow": _signal_summary(slow_signal, slow_hz),
        },
        "matrixAction": decision.to_dict() if decision else {"action": "hold", "reason": "no engine"},
        "pendingApproval": pending_approval,
        "agdel": agdel_buyer.get_stats() if agdel_buyer else {},
        "availableSignals": agdel_buyer.get_available_enriched() if agdel_buyer else [],
        "portfolio": portfolio,
        "wallet": {
            **(agdel_buyer.get_wallet_info() if agdel_buyer else {}),
            "hlEquity": portfolio.get("equity", 0),
            "hlAvailable": portfolio.get("availableBalance", 0),
        },
        "predictions": _build_predictions(),
        "purchases": list(agdel_buyer.purchase_log) if agdel_buyer else [],
    }

    latest_state = state
    tick_history.appendleft({"timestamp": now, "markPrice": mark_price})
    await broadcast(state)


def _signal_summary(sig: dict | None, horizon: str) -> dict:
    if not sig:
        return {"horizon": horizon, "score": 0, "confidence": 0, "state": "NONE", "active": False}
    from matrix_engine import classify_signal_state
    mc = config.get("matrix", {})
    state = classify_signal_state(
        sig.get("score", 0), sig.get("confidence", 0),
        mc.get("confidentThreshold", 0.30), mc.get("flatThreshold", 0.03),
    )
    return {
        "horizon": sig.get("horizon", horizon),
        "score": round(sig.get("score", 0), 4),
        "confidence": round(sig.get("confidence", 0), 4),
        "direction": sig.get("direction", ""),
        "state": state,
        "maker": str(sig.get("maker", ""))[:12],
        "cost": sig.get("cost_usdc", 0),
        "age": round(time.time() - sig.get("received_at", time.time())),
        "active": True,
    }


async def agdel_poll_loop():
    if not agdel_buyer or not agdel_buyer.enabled:
        return
    interval = agdel_buyer.poll_interval
    await asyncio.sleep(5)
    poll_count = 0
    while True:
        try:
            purchased = await agdel_buyer.poll_once()
            if purchased:
                logger.info("AGDEL: purchased %d signals", len(purchased))
            await agdel_buyer.check_stale_deliveries()
            # Check outcomes less frequently (every ~60s) to keep poll loop fast
            poll_count += 1
            if poll_count % 4 == 0:
                await agdel_buyer.check_outcomes()
        except Exception as e:
            logger.error("AGDEL poll error: %s", e)
        await asyncio.sleep(interval)


async def broadcast(data: dict):
    if not connected_clients:
        return
    message = json.dumps(data, default=str)
    disconnected = set()
    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        connected_clients.discard(ws)


# ── FastAPI app ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global matrix_engine, agdel_buyer, hl_trader
    global trading_mode, _tick_task, _agdel_task

    load_config()
    trading_mode = "live" if config.get("trading", {}).get("enable", False) else "paper"

    matrix_engine = MatrixEngine(config)
    agdel_buyer = AgdelBuyer(config)
    hl_trader = HLTrader(config, mode=trading_mode)

    await hl_trader.connect()
    await agdel_buyer.start()

    _tick_task = asyncio.create_task(tick_loop())
    _agdel_task = asyncio.create_task(agdel_poll_loop())

    logger.info("Server started (mode=%s, asset=%s)", trading_mode,
                config.get("trading", {}).get("assets", ["ETH"])[0])
    yield

    for task in (_tick_task, _agdel_task):
        if task:
            task.cancel()
    await agdel_buyer.stop()


app = FastAPI(lifespan=lifespan)


def _price_at_time(ts: float) -> float | None:
    """Find the closest tick price to a given timestamp."""
    if not ts or not tick_history:
        return None
    best = None
    best_delta = float("inf")
    for tick in tick_history:
        delta = abs(tick["timestamp"] - ts)
        if delta < best_delta:
            best_delta = delta
            best = tick["markPrice"]
    return best


def _build_predictions() -> list[dict]:
    """Build prediction list from purchase_log for the gravity chart."""
    now = time.time()
    preds = []
    if not agdel_buyer:
        return preds
    for entry in agdel_buyer.purchase_log:
        if not entry.get("delivered"):
            continue
        tp = entry.get("target_price")
        if not tp:
            continue
        expiry = entry.get("expiry_time", 0)
        if expiry and expiry < now - 30 * 60:
            continue
        d = entry.get("direction")
        direction = "long" if d in (0, "0", "long") else "short" if d in (1, "1", "short") else str(d)
        expired = bool(expiry and expiry < now)
        outcome = entry.get("outcome", "")
        purchased_at = float(entry.get("purchased_at", 0) or 0)
        created_at = float(entry.get("created_at", 0) or 0)
        raw_entry = entry.get("entry_price")
        if raw_entry is not None:
            ep = float(raw_entry)
            entry_price = ep / 1e8 if ep > 1e6 else ep
        else:
            entry_price = _price_at_time(purchased_at) if purchased_at else None
        preds.append({
            "targetPrice": float(tp),
            "expiryTime": float(expiry),
            "direction": direction,
            "confCalib": float(entry.get("conf_calib", 0)),
            "qualityScore": float(entry.get("quality_score", 0.5)),
            "horizon": entry.get("horizon", ""),
            "hash": entry.get("commitment_hash", "")[:10],
            "expired": expired,
            "outcome": outcome,
            "entryTime": created_at or purchased_at,
            "entryPrice": entry_price,
        })
    return preds


# ── Routes ───────────────────────────────────────────────────────────

@app.get("/")
async def dashboard():
    return FileResponse("dashboard.html", media_type="text/html")


@app.get("/dashboard.css")
async def dashboard_css():
    return FileResponse("dashboard.css", media_type="text/css")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    if latest_state:
        await ws.send_text(json.dumps(latest_state, default=str))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(ws)


@app.get("/api/state")
async def get_state():
    return JSONResponse(latest_state or {"status": "initializing"})


@app.post("/api/config/mode")
async def set_mode(body: dict):
    global trading_mode
    mode = body.get("mode", "")
    if mode not in ("paper", "live"):
        return JSONResponse({"error": "mode must be 'paper' or 'live'"}, status_code=400)
    trading_mode = mode
    if hl_trader:
        hl_trader.set_mode(mode)
        if mode == "live":
            await hl_trader.connect()
    logger.info("Trading mode set to: %s", mode)
    return JSONResponse({"mode": mode})


@app.post("/api/approve")
async def approve_trade():
    """Human approves the pending matrix recommendation — execute the trade."""
    global pending_approval
    if not pending_approval:
        return JSONResponse({"ok": False, "error": "No pending trade"}, status_code=400)
    if not hl_trader:
        return JSONResponse({"ok": False, "error": "Trader not ready"}, status_code=503)

    action = pending_approval["action"]
    size_pct = pending_approval["size_pct"]
    mark_price = await hl_trader.get_mark_price()

    result = await hl_trader.execute(action, size_pct, mark_price)
    if result and result.success:
        if matrix_engine:
            matrix_engine.record_action(action)
        trade_history.appendleft(result.to_dict())
        pending_approval = None
        logger.info("Trade APPROVED and executed: %s", action)
        return JSONResponse({"ok": True, "trade": result.to_dict()})
    else:
        error = result.error if result else "execution failed"
        return JSONResponse({"ok": False, "error": error}, status_code=500)


@app.post("/api/close")
async def close_position():
    """Directly close the HL position regardless of matrix state."""
    if not hl_trader:
        return JSONResponse({"ok": False, "error": "Trader not ready"}, status_code=503)
    if hl_trader.mode != "live":
        return JSONResponse({"ok": False, "error": "Not in live mode"}, status_code=400)
    mark_price = await hl_trader.get_mark_price()
    result = await hl_trader.execute("close", 0, mark_price)
    if result and result.success:
        trade_history.appendleft(result.to_dict())
        logger.info("Direct CLOSE executed")
        return JSONResponse({"ok": True, "trade": result.to_dict()})
    else:
        error = result.error if result else "close failed"
        logger.error("Direct close failed: %s", error)
        return JSONResponse({"ok": False, "error": error}, status_code=500)


@app.post("/api/reject")
async def reject_trade():
    """Human rejects the pending matrix recommendation."""
    global pending_approval
    if not pending_approval:
        return JSONResponse({"ok": False, "error": "No pending trade"}, status_code=400)
    logger.info("Trade REJECTED: %s", pending_approval["action"])
    pending_approval = None
    return JSONResponse({"ok": True})


@app.get("/api/ticks")
async def get_ticks():
    """Return tick_history as [{timestamp, markPrice}] in chronological order."""
    ticks = [{"timestamp": t["timestamp"], "markPrice": t["markPrice"]} for t in tick_history]
    ticks.reverse()
    return JSONResponse(ticks)


@app.get("/api/predictions")
async def get_predictions():
    """Return active delivered predictions from purchase_log."""
    return JSONResponse(_build_predictions())


@app.get("/api/trades")
async def get_trades():
    return JSONResponse(list(trade_history))


@app.get("/api/agdel/available")
async def get_available_signals():
    if agdel_buyer:
        return JSONResponse(agdel_buyer.available_signals[:20])
    return JSONResponse([])


@app.get("/api/agdel/purchases")
async def get_purchases():
    if agdel_buyer:
        return JSONResponse(list(agdel_buyer.purchase_log))
    return JSONResponse([])


@app.post("/api/agdel/webhook/delivery")
async def agdel_webhook_delivery(body: dict):
    if not agdel_buyer:
        return JSONResponse({"ok": False}, status_code=503)
    event = body.get("event", "")
    logger.info("Webhook POST: event=%s hash=%s maker=%s",
                event, body.get("commitment_hash", "")[:12],
                body.get("maker_address", "")[:12])
    if event == "delivery":
        signal = await agdel_buyer.handle_webhook_delivery(body)
        if signal:
            logger.info("Webhook delivery processed: %s %s", signal.get("horizon"), signal.get("direction"))
        else:
            logger.warning("Webhook delivery not matched (pending=%d): %s",
                          len(agdel_buyer._pending_deliveries),
                          body.get("commitment_hash", "")[:12])
        return JSONResponse({"ok": True, "delivered": signal is not None})
    elif event == "resolution":
        updated = agdel_buyer.handle_webhook_resolution(body)
        if updated:
            await broadcast({"type": "resolution", "commitment_hash": body.get("commitment_hash", ""), "outcome": updated.get("outcome")})
        return JSONResponse({"ok": True, "resolved": updated is not None})
    else:
        return JSONResponse({"ok": True, "skipped": True})


@app.post("/api/agdel/buy")
async def manual_buy(body: dict):
    commitment_hash = body.get("commitment_hash", "")
    if not commitment_hash:
        return JSONResponse({"ok": False, "error": "commitment_hash required"}, status_code=400)
    if not agdel_buyer:
        return JSONResponse({"ok": False, "error": "Buyer not ready"}, status_code=503)
    result = await agdel_buyer.manual_purchase(commitment_hash)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/api/agdel/signal/{commitment_hash}")
async def get_signal_detail(commitment_hash: str):
    if not agdel_buyer:
        return JSONResponse({"error": "AGDEL buyer not initialized"}, status_code=503)
    result = await agdel_buyer.get_signal_detail(commitment_hash)
    content = json.loads(json.dumps(result, default=str))
    return JSONResponse(content)


@app.get("/api/reflection/history")
async def get_reflection_history():
    return JSONResponse({"status": {}, "history": []})


@app.post("/api/agdel/budget/reset")
async def reset_budget():
    if not agdel_buyer:
        return JSONResponse({"ok": False}, status_code=503)
    agdel_buyer.budget._hourly_spend = 0.0
    agdel_buyer.budget._daily_spend = 0.0
    agdel_buyer.budget._hourly_reset = time.time()
    agdel_buyer.budget._daily_reset = time.time()
    return JSONResponse({"ok": True, "budget": agdel_buyer.budget.status()})


@app.post("/api/agdel/autobuy")
async def toggle_autobuy():
    if not agdel_buyer:
        return JSONResponse({"ok": False}, status_code=503)
    agdel_buyer.auto_buy = not agdel_buyer.auto_buy
    logger.info("Auto-buy toggled: %s", agdel_buyer.auto_buy)
    return JSONResponse({"ok": True, "autoBuy": agdel_buyer.auto_buy})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9004)
