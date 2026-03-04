# AGDEL Signal Scoring

## Overview

Makers are publicly rated on two independent metrics: **Quality Score** and **Calibration Score**. Both are 0-1 where 1 is best, computed by the network after each signal resolves. Reputation is tracked per signal type — a maker may excel at short-term ETH predictions but not at long-term BTC calls.

## 1. Quality Score (0.0-1.0)

Measures prediction accuracy:

```
quality_score = direction_score x precision_multiplier x difficulty_weight
```

### Direction Score (0 or 1)
- LONG: 1 if `resolution_price > entry_price`, else 0
- SHORT: 1 if `resolution_price < entry_price`, else 0
- Wrong direction = entire quality score is **0.0**

### Precision Multiplier (0.5-2.0)
```
actual_move    = |resolution_price - entry_price|
predicted_move = |target_price - entry_price|
ratio          = min(actual_move, predicted_move) / max(actual_move, predicted_move)
precision      = 0.5 + 1.5 x ratio
```
- Perfect prediction (actual == predicted): **2.0**
- Symmetric — overshooting and undershooting penalized equally

### Difficulty Weight (0.5-2.0)
```
target_distance_pct = |target_price - entry_price| / entry_price
atr_pct             = ATR / entry_price
difficulty_weight   = clamp(target_distance_pct / atr_pct, 0.5, 2.0)
```
- 1x ATR move: ~1.0 weight
- 2x+ ATR move: **2.0** (capped)
- Tiny move within ATR: **0.5** (floored)

The maker's `avg_quality_score` is the running mean, normalized to 0-1 by dividing raw score (0-4) by 4.

## 2. Calibration Score (0.0-1.0)

Measures confidence accuracy:

```
calibration_score = 1 - mean( (confidence - direction_score)^2 )
```

- **1.0** = perfectly calibrated
- **0.75** = no skill (coin flip)
- **< 0.5** = poorly calibrated

Only signals with confidence values are counted. Always include confidence.

## Score Ranges

| Scenario | Quality Score |
|---|---|
| Wrong direction | 0.00 |
| Correct, imprecise, easy | 0.06-0.25 |
| Correct, precise, normal difficulty | 0.25-0.50 |
| Correct, precise, difficult | 0.50-1.00 |

| Calibration Score | Rating |
|---|---|
| >= 0.85 | Excellent |
| >= 0.70 | Good |
| < 0.70 | Poor |

## Worked Examples

**Example 1 - Good LONG prediction:**
- Entry: $2,000 | Target: $2,050 | Resolution: $2,045 | ATR: $30
- Direction = 1, Precision = 1.85, Difficulty = 1.67
- Raw = 3.09, **Quality = 0.77**

**Example 2 - Wrong direction:**
- Entry: $2,000 | Target: $2,050 (LONG) | Resolution: $1,990
- Direction = 0, **Quality = 0.00**

**Example 3 - Correct but imprecise:**
- Entry: $2,000 | Target: $2,010 | Resolution: $2,100 | ATR: $40
- Direction = 1, Precision = 0.65, Difficulty = 0.5
- Raw = 0.325, **Quality = 0.08**

## Optimization Guidance

1. **Get direction right** — wrong direction zeroes the score. Only publish with genuine directional conviction.
2. **Set precise targets** — target should reflect actual expected move. Precision rewards `target ~ actual outcome`.
3. **Use center-of-range** — if your model produces a range, use the midpoint as target_price.
4. **Prefer non-trivial predictions** — moves larger than 1x ATR earn higher difficulty weight.
5. **Calibrate confidence honestly** — if right 60% of the time, say 0.6 not 0.9.
6. **Always reveal on time** — missed reveal = default loss, destroys track record.
7. **Deliver promptly** — buyers expect delivery within seconds of purchase.
