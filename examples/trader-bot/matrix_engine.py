"""Matrix decision engine — deterministic signal classification and position action lookup."""

from __future__ import annotations

import time
from dataclasses import dataclass


def classify_signal_state(
    score: float,
    confidence: float,
    conf_threshold: float = 0.30,
    flat_threshold: float = 0.03,
) -> str:
    """Classify a single signal into one of 5 discrete states."""
    if abs(score) <= flat_threshold:
        return "FLAT"
    if score > 0:
        return "STRONG_LONG" if confidence >= conf_threshold else "LEAN_LONG"
    return "STRONG_SHORT" if confidence >= conf_threshold else "LEAN_SHORT"


# ── Decision tables: (fast_state, slow_state) → (action, size_tier) ──

_MATRIX_TABLE_FLAT: dict[tuple[str, str], tuple[str, str]] = {
    ("STRONG_LONG",  "STRONG_LONG"):  ("open_long",  "max"),
    ("STRONG_LONG",  "LEAN_LONG"):    ("open_long",  "partial"),
    ("STRONG_LONG",  "FLAT"):         ("hold",       "none"),
    ("STRONG_LONG",  "LEAN_SHORT"):   ("hold",       "none"),
    ("STRONG_LONG",  "STRONG_SHORT"): ("hold",       "none"),
    ("LEAN_LONG",    "STRONG_LONG"):  ("open_long",  "partial"),
    ("LEAN_LONG",    "LEAN_LONG"):    ("open_long",  "partial"),
    ("LEAN_LONG",    "FLAT"):         ("hold",       "none"),
    ("LEAN_LONG",    "LEAN_SHORT"):   ("hold",       "none"),
    ("LEAN_LONG",    "STRONG_SHORT"): ("hold",       "none"),
    ("FLAT",         "STRONG_LONG"):  ("hold",       "none"),
    ("FLAT",         "LEAN_LONG"):    ("hold",       "none"),
    ("FLAT",         "FLAT"):         ("hold",       "none"),
    ("FLAT",         "LEAN_SHORT"):   ("hold",       "none"),
    ("FLAT",         "STRONG_SHORT"): ("hold",       "none"),
    ("LEAN_SHORT",   "STRONG_LONG"):  ("hold",       "none"),
    ("LEAN_SHORT",   "LEAN_LONG"):    ("hold",       "none"),
    ("LEAN_SHORT",   "FLAT"):         ("hold",       "none"),
    ("LEAN_SHORT",   "LEAN_SHORT"):   ("open_short", "partial"),
    ("LEAN_SHORT",   "STRONG_SHORT"): ("open_short", "partial"),
    ("STRONG_SHORT", "STRONG_LONG"):  ("hold",       "none"),
    ("STRONG_SHORT", "LEAN_LONG"):    ("hold",       "none"),
    ("STRONG_SHORT", "FLAT"):         ("hold",       "none"),
    ("STRONG_SHORT", "LEAN_SHORT"):   ("open_short", "partial"),
    ("STRONG_SHORT", "STRONG_SHORT"): ("open_short", "max"),
}

_MATRIX_TABLE_LONG: dict[tuple[str, str], tuple[str, str]] = {
    ("STRONG_LONG",  "STRONG_LONG"):  ("increase",       "max"),
    ("STRONG_LONG",  "LEAN_LONG"):    ("hold",           "none"),
    ("STRONG_LONG",  "FLAT"):         ("hold",           "none"),
    ("STRONG_LONG",  "LEAN_SHORT"):   ("decrease_long",  "reduce"),
    ("STRONG_LONG",  "STRONG_SHORT"): ("close",          "none"),
    ("LEAN_LONG",    "STRONG_LONG"):  ("hold",           "none"),
    ("LEAN_LONG",    "LEAN_LONG"):    ("hold",           "none"),
    ("LEAN_LONG",    "FLAT"):         ("hold",           "none"),
    ("LEAN_LONG",    "LEAN_SHORT"):   ("decrease_long",  "reduce"),
    ("LEAN_LONG",    "STRONG_SHORT"): ("close",          "none"),
    ("FLAT",         "STRONG_LONG"):  ("hold",           "none"),
    ("FLAT",         "LEAN_LONG"):    ("hold",           "none"),
    ("FLAT",         "FLAT"):         ("hold",           "none"),
    ("FLAT",         "LEAN_SHORT"):   ("decrease_long",  "reduce"),
    ("FLAT",         "STRONG_SHORT"): ("close",          "none"),
    ("LEAN_SHORT",   "STRONG_LONG"):  ("decrease_long",  "reduce"),
    ("LEAN_SHORT",   "LEAN_LONG"):    ("decrease_long",  "reduce"),
    ("LEAN_SHORT",   "FLAT"):         ("decrease_long",  "reduce"),
    ("LEAN_SHORT",   "LEAN_SHORT"):   ("close",          "none"),
    ("LEAN_SHORT",   "STRONG_SHORT"): ("flip_short",     "partial"),
    ("STRONG_SHORT", "STRONG_LONG"):  ("close",          "none"),
    ("STRONG_SHORT", "LEAN_LONG"):    ("close",          "none"),
    ("STRONG_SHORT", "FLAT"):         ("close",          "none"),
    ("STRONG_SHORT", "LEAN_SHORT"):   ("flip_short",     "partial"),
    ("STRONG_SHORT", "STRONG_SHORT"): ("flip_short",     "max"),
}

_MATRIX_TABLE_SHORT: dict[tuple[str, str], tuple[str, str]] = {
    ("STRONG_LONG",  "STRONG_LONG"):  ("flip_long",      "max"),
    ("STRONG_LONG",  "LEAN_LONG"):    ("flip_long",      "partial"),
    ("STRONG_LONG",  "FLAT"):         ("close",          "none"),
    ("STRONG_LONG",  "LEAN_SHORT"):   ("close",          "none"),
    ("STRONG_LONG",  "STRONG_SHORT"): ("close",          "none"),
    ("LEAN_LONG",    "STRONG_LONG"):  ("flip_long",      "partial"),
    ("LEAN_LONG",    "LEAN_LONG"):    ("close",          "none"),
    ("LEAN_LONG",    "FLAT"):         ("close",          "none"),
    ("LEAN_LONG",    "LEAN_SHORT"):   ("decrease_short",  "reduce"),
    ("LEAN_LONG",    "STRONG_SHORT"): ("decrease_short",  "reduce"),
    ("FLAT",         "STRONG_LONG"):  ("close",          "none"),
    ("FLAT",         "LEAN_LONG"):    ("close",          "none"),
    ("FLAT",         "FLAT"):         ("hold",           "none"),
    ("FLAT",         "LEAN_SHORT"):   ("hold",           "none"),
    ("FLAT",         "STRONG_SHORT"): ("hold",           "none"),
    ("LEAN_SHORT",   "STRONG_LONG"):  ("close",          "none"),
    ("LEAN_SHORT",   "LEAN_LONG"):    ("decrease_short",  "reduce"),
    ("LEAN_SHORT",   "FLAT"):         ("hold",           "none"),
    ("LEAN_SHORT",   "LEAN_SHORT"):   ("hold",           "none"),
    ("LEAN_SHORT",   "STRONG_SHORT"): ("hold",           "none"),
    ("STRONG_SHORT", "STRONG_LONG"):  ("close",          "none"),
    ("STRONG_SHORT", "LEAN_LONG"):    ("decrease_short",  "reduce"),
    ("STRONG_SHORT", "FLAT"):         ("hold",           "none"),
    ("STRONG_SHORT", "LEAN_SHORT"):   ("hold",           "none"),
    ("STRONG_SHORT", "STRONG_SHORT"): ("increase",       "max"),
}

MATRIX_TABLE: dict[str, dict[tuple[str, str], tuple[str, str]]] = {
    "flat": _MATRIX_TABLE_FLAT,
    "long": _MATRIX_TABLE_LONG,
    "short": _MATRIX_TABLE_SHORT,
}

SIZE_TIERS: dict[str, float] = {
    "max": 1.0,
    "partial": 0.5,
    "reduce": 0.5,
    "none": 0.0,
}


@dataclass
class MatrixDecision:
    action: str       # open_long, open_short, increase, decrease_long, decrease_short,
                      # flip_long, flip_short, close, hold
    size_tier: str    # max, partial, reduce, none
    size_pct: float   # 0.0 - 1.0
    fast_state: str
    slow_state: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "sizeTier": self.size_tier,
            "sizePct": self.size_pct,
            "fastState": self.fast_state,
            "slowState": self.slow_state,
            "reason": self.reason,
        }


class MatrixEngine:
    """Deterministic 3x3 matrix decision engine."""

    def __init__(self, config: dict):
        mc = config.get("matrix", {})
        self.conf_threshold: float = mc.get("confidentThreshold", 0.30)
        self.flat_threshold: float = mc.get("flatThreshold", 0.03)
        self.max_size_pct: float = mc.get("maxSizePct", 1.0)
        self.partial_size_pct: float = mc.get("partialSizePct", 0.5)
        self.reduce_pct: float = mc.get("reducePct", 0.5)
        self.initial_size_pct: float = mc.get("initialSizePct", 0.15)
        self.increase_step_pct: float = mc.get("increaseStepPct", 0.20)
        self.increase_cooldown_ms: float = mc.get("increaseCooldownMs", 120_000)
        self.min_hold_ms: float = mc.get("matrixMinHoldMs", 120_000)
        self.flip_cooldown_ms: float = mc.get("matrixFlipCooldownMs", 300_000)

        self._last_increase_at: float = 0
        self._last_flip_at: float = 0
        self._position_opened_at: float = 0

    def decide(
        self,
        fast_signal: dict | None,
        slow_signal: dict | None,
        position: dict | None,
    ) -> MatrixDecision:
        """Compute matrix action from fast/slow signals and current position."""
        now_ms = time.time() * 1000

        # Classify signals
        if fast_signal and fast_signal.get("score") is not None:
            fast_state = classify_signal_state(
                fast_signal["score"], fast_signal.get("confidence", 0),
                self.conf_threshold, self.flat_threshold,
            )
        else:
            fast_state = "FLAT"

        if slow_signal and slow_signal.get("score") is not None:
            slow_state = classify_signal_state(
                slow_signal["score"], slow_signal.get("confidence", 0),
                self.conf_threshold, self.flat_threshold,
            )
        else:
            slow_state = "FLAT"

        # Determine position state
        if position and position.get("size", 0) != 0:
            pos_state = "long" if position["size"] > 0 else "short"
        else:
            pos_state = "flat"

        # Matrix lookup
        table = MATRIX_TABLE.get(pos_state, _MATRIX_TABLE_FLAT)
        action, size_tier = table.get((fast_state, slow_state), ("hold", "none"))

        # Apply cooldowns and guards
        reason_parts = [f"fast={fast_state}, slow={slow_state}, pos={pos_state}"]

        # Hold-period guard: don't close/flip too soon after opening
        if pos_state != "flat" and action in ("close", "flip_long", "flip_short"):
            elapsed = now_ms - self._position_opened_at
            if elapsed < self.min_hold_ms:
                reason_parts.append(f"hold-guard ({elapsed:.0f}/{self.min_hold_ms:.0f}ms)")
                action, size_tier = "hold", "none"

        # Increase cooldown
        if action == "increase":
            elapsed = now_ms - self._last_increase_at
            if elapsed < self.increase_cooldown_ms:
                reason_parts.append(f"increase-cooldown ({elapsed:.0f}/{self.increase_cooldown_ms:.0f}ms)")
                action, size_tier = "hold", "none"

        # Flip cooldown
        if action in ("flip_long", "flip_short"):
            elapsed = now_ms - self._last_flip_at
            if elapsed < self.flip_cooldown_ms:
                reason_parts.append(f"flip-cooldown ({elapsed:.0f}/{self.flip_cooldown_ms:.0f}ms)")
                action, size_tier = "hold", "none"

        size_pct = SIZE_TIERS.get(size_tier, 0.0)
        reason = " | ".join(reason_parts)

        return MatrixDecision(
            action=action,
            size_tier=size_tier,
            size_pct=size_pct,
            fast_state=fast_state,
            slow_state=slow_state,
            reason=reason,
        )

    def record_action(self, action: str):
        """Update internal timestamps after an action executes."""
        now_ms = time.time() * 1000
        if action == "increase":
            self._last_increase_at = now_ms
        elif action in ("flip_long", "flip_short"):
            self._last_flip_at = now_ms
        if action in ("open_long", "open_short", "flip_long", "flip_short"):
            self._position_opened_at = now_ms

    def update_thresholds(self, **kwargs):
        """Update thresholds from reflection. Accepts any config key."""
        for key, val in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, val)
