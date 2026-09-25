# Bloodaxe — Disciplined Short-Premium Iron Condors

**Bloodaxe is a systematic short-premium iron condor strategy** focused on
capturing the volatility risk premium (VRP) across multiple liquid
underlyings with strict event avoidance, expected-move-based strike
selection, and pre-defined exits.

Launched 2026-09-25 as a sibling book to VIDAR. Bloodaxe is the more
disciplined of the two — multi-underlying, event-aware, fixed-risk, with
explicit exits before entry.

## The 9 Bloodaxe Rules

Every trade follows these rules in order:

1. **Find liquid underlying** — only trade underlyings with tight
   option bid/ask spreads, sufficient volume, and a deep option chain
   (>=5 strikes each side of ATM).

2. **Avoid major binary events** — skip earnings (within 7 days), FOMC,
   CPI, NFP, and other scheduled catalysts unless deliberately
   trading the event.

3. **Establish expected move** — compute the expected move for the
   chosen DTE using ATM straddle or IV: `em = spot * iv * sqrt(DTE/365)`.

4. **Identify reasonable upper/lower range** — strikes sit *outside* the
   expected move so we have a structural edge from selling vol above
   realized. Target 16-delta short wings.

5. **Sell an iron condor around that range** — short_put at lower
   strike, short_call at upper strike, with $5-wide wings (or $2-wide
   for low-priced underlyings).

6. **Keep wings close enough that max loss is genuinely acceptable** —
   wing_width × 100 × qty ≤ $500. Default 1 contract at $5 wings = $500
   max risk ceiling.

7. **Define the exit before entering** — profit-take 50% of credit,
   stop-loss 2x credit, time-stop 7 DTE, wing-breach defensive close.

8. **Risk a small, consistent amount per trade** — fixed $500/condor,
   regardless of underlying. Don't size up after wins.

9. **Keep a trade journal** — every open/close/exit logged to
   `journal.jsonl` with full context (strike selection rationale, IV at
   entry, exit reason).

## Why

- **VRP is structural** — Carr & Wu (2009) shows implied vol is
  persistently ~2 vol points above realized, ~8-12% systematic annual
  return for short-vol strategies.
- **Event avoidance** — selling vol into known catalysts has a left tail
  (gap risk on earnings, policy shocks). Filter them out.
- **Expected-move strike selection** — strike placement relative to
  expected move determines P&L distribution; outside-EM strikes have
  higher probability of profit.
- **Pre-defined exits** — discipline at exit is half the edge. Force
  the operator (or the cron) to act on the rule, not the gut.

## Why not VIDAR

VIDAR is the same idea but:
- VIDAR is SPY-only, Bloodaxe is multi-underlying
- VIDAR picks 16-delta strikes directly from the chain, Bloodaxe
  computes expected move first then places strikes outside it
- VIDAR has 50% profit / 2x stop / DTE-7 exit (same as Bloodaxe), but
  Bloodaxe adds the journal discipline and event avoidance

The two can run in parallel — VIDAR is the high-conviction SPY book,
Bloodaxe is the diversified basket.

## Expected return

Based on VRP research:
- Gross: 8-12% annualized
- Net of $1/contract commissions + 1-2% slippage: 6-10% annualized
- Sharpe: 0.4-0.7 typical

## Risk envelope

- **Max loss per trade**: $500 (1 contract, $5 wings on most underlyings)
- **Default contracts per trade**: 1
- **Max concurrent positions**: 5 (diversification)
- **Max single-day loss**: $500
- **Expected max DD over 12 months**: 15-25% normal, 25-35% in crash regimes

## Layout

```
bloodaxe/
├── README.md                    # This file
├── cli/scan.py                   # Daily scanner: multi-underlying, event-aware
├── bloodaxe_pkg/
│   ├── scanner/                  # Rule 1-2: liquidity + event filters
│   │   ├── liquidity.py          # Bid/ask spread, volume, chain depth
│   │   └── events.py             # Earnings calendar, FOMC/CPI/NFP blackout
│   ├── expected_move/            # Rule 3: IV-based expected move
│   │   └── calc.py
│   ├── strikes/                  # Rule 4-5: 16-delta strikes outside EM
│   │   └── picker.py
│   ├── sizing/                   # Rule 6, 8: $500 max risk, fixed risk
│   │   └── caps.py
│   ├── exits/                    # Rule 7: profit/stop/time exits
│   │   └── ladder.py
│   └── journal/                  # Rule 9: trade journal
│       └── log.py
├── ragnar_scripts/
│   ├── bloodaxe_auto.py          # Phase orchestrator (pre_build/open/exit_review)
│   ├── ragnar_specs/             # Daily specs (JSON)
│   └── install_bloodaxe_cron.py  # Cron installer
├── tests/                        # Regression tests for each rule
└── scripts/
    ├── arm_bloodaxe_paper.sh
    └── disarm_bloodaxe_paper.sh
```

## Usage

```bash
# Scan + emit today's spec
python3 /home/freya/bloodaxe/cli/scan.py --out /home/freya/bloodaxe/ragnar_scripts/ragnar_specs/bloodaxe_$(date +%Y%m%d).json

# Open the position
python3 /home/freya/bloodaxe/ragnar_scripts/bloodaxe_auto.py open

# Exit review (every 20min during RTH)
python3 /home/freya/bloodaxe/ragnar_scripts/bloodaxe_auto.py exit_review
```

## Cron schedule (paper-only on first deploy)

```
0 1 * * 1-5   bloodaxe_auto.py pre_build     # 09:00 ET, before RTH open
35 1 * * 1-5  bloodaxe_auto.py open          # 09:35 ET, just after RTH open
*/20 1-8 * * 1-5  bloodaxe_auto.py exit_review  # every 20m during RTH
```

## Audit log

Shares `/home/freya/RAGNAR/verticals_bot_audit.jsonl` with other
strategies (VIDAR, DEZ, RAGNAR, FREYA, GUNNAR, TORVALD). All Bloodaxe
events tagged `strategy=bloodaxe`, `tag=bloodaxe.ic`, `scope=bloodaxe`.

## Trade journal

Every Bloodaxe trade appends to `bloodaxe/journal.jsonl` with:
- entry timestamp, underlying, expiry, strikes
- IV at entry, expected move, net credit
- max loss (wing width × 100)
- exit trigger, exit timestamp, exit reason, realized P&L
