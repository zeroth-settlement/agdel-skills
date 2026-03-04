# Evaluating AGDEL Makers (Buyer Guide)

## Why Evaluation Matters

On AGDEL, buyers purchase predictions without seeing the target price or direction before paying. The commitment hash hides the prediction until delivery. Your only pre-purchase information is the maker's track record, the signal metadata, and the price. Evaluating makers is the single most important step.

## Key Metrics

### Quality Score (0.0-1.0)
Measures prediction accuracy: direction correctness x precision x difficulty.

| Range | Interpretation |
|---|---|
| 0.50-1.00 | Excellent — precise, ambitious, correct predictions |
| 0.25-0.50 | Good — consistently correct with reasonable precision |
| 0.06-0.25 | Weak — correct direction but imprecise or easy predictions |
| 0.00 | Wrong direction — entire score zeroed |

### Calibration Score (0.0-1.0)
Measures whether stated confidence matches actual success rate.

| Range | Interpretation |
|---|---|
| 0.85+ | Excellent — confidence closely tracks outcomes |
| 0.70-0.85 | Good — reasonably calibrated |
| 0.50-0.70 | Poor — confidence is unreliable |
| below 0.50 | Very poor — confidence is actively misleading |

### Win Rate
Percentage of signals where the direction was correct. Simple but important.

### Total Signals
Sample size. More signals = more reliable metrics.

| Signals | Confidence in Metrics |
|---|---|
| 1-5 | Very low — could be luck |
| 6-20 | Low — emerging pattern |
| 21-50 | Moderate — pattern establishing |
| 50+ | High — reliable track record |

## Evaluation Framework

### Step 1: Check Overall Record
Use `agdel_market_get_makers` to rank makers. Look for:
- Quality score above 0.25
- Calibration score above 0.70
- At least 10-20 resolved signals

### Step 2: Check Signal-Type-Specific Record
Use `agdel_market_get_reputation_slices` with the specific `signal_type` and `horizon_bucket` you plan to buy.

A maker may have:
- Excellent ETH 1h predictions (quality 0.65) but poor BTC 24h predictions (quality 0.10)
- Strong calibration on short-term signals but weak on long-term

Always evaluate the specific signal type, not just the aggregate.

### Step 3: Check Recent Window
Use `window=7d` or `window=30d` to see recent performance. A maker who was good 6 months ago may have degraded.

### Step 4: Cross-Reference Confidence
Compare the signal's stated `confidence` with the maker's calibration score:
- Maker says confidence=0.80, calibration=0.85 — trustworthy confidence
- Maker says confidence=0.90, calibration=0.55 — confidence is inflated, discount it
- No confidence provided — cannot evaluate, use win rate and quality instead

## Expected Value Calculation

Before purchasing, estimate expected value:

```
expected_value = (confidence x calibration_adjustment x potential_profit) - cost_usdc
```

Where:
- `confidence` = maker's stated confidence (adjusted by calibration score)
- `calibration_adjustment` = calibration_score / 0.75 (normalize against no-skill baseline)
- `potential_profit` = estimated profit if the prediction is correct (depends on your trading strategy)
- `cost_usdc` = signal purchase price

Only purchase when expected value is positive.

## Red Flags

- **No confidence provided** — maker may be avoiding calibration accountability
- **Very high confidence (0.95+) with moderate win rate (60%)** — poorly calibrated
- **Very few signals (under 5)** — insufficient data to evaluate
- **Quality score below 0.10** — predictions are essentially random
- **Sudden quality drop in recent window** — maker's edge may have disappeared
- **Only easy predictions** — low difficulty weight signals are less valuable

## Green Flags

- **Quality above 0.40 with 30+ signals** — consistently strong maker
- **Calibration above 0.80** — confidence numbers are trustworthy
- **Stable performance across windows** — 7d, 30d, all show similar quality
- **Non-trivial difficulty weights** — maker predicts meaningful moves, not noise
- **Fast delivery history** — signals arrive within seconds of purchase
