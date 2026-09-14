# XAUUSD 1-Minute Scalping Bot

A backtesting + paper-trading engine for a 1-minute EMA-crossover / RSI-filtered
scalping strategy on XAUUSD (spot gold). **This does not place real trades or
connect to any broker.** It runs entirely against CSV candle data you supply
(or synthetic data it can generate for you), so you can develop and validate
the strategy before ever risking money on it.

> ⚠️ **Disclaimer:** This is a research/education tool, not financial advice.
> Backtested and paper-traded performance does not guarantee future results.
> The cost model (spread, commission, slippage) is a simplification of real
> execution conditions — real fills, especially during news or low liquidity,
> can be significantly worse. If you eventually wire this into live/demo
> execution via a broker API, start on a demo account and understand the
> risks before using real funds.

## Strategy

On each closed 1-minute bar:
- **Long** when the fast EMA crosses above the slow EMA while RSI confirms
  bullish momentum without being overbought.
- **Short** when the fast EMA crosses below the slow EMA while RSI confirms
  bearish momentum without being oversold.
- Optionally restricted to the London/New York session overlap (UTC), since
  XAUUSD spreads widen outside it enough to erode a scalp's edge.
- Stop-loss and take-profit are ATR-based (`sl = ATR * atr_sl_mult`,
  `tp = ATR * atr_tp_mult`), sized at the bar the signal fired.

All of this is configurable — see `xauusd_scalper/config.py` for every
parameter and its default, or pass a JSON `--config` file / CLI flags (see
below).

A signal computed from a bar's close is only ever executed at the **next**
bar's open, and stop-loss/take-profit are checked against that bar's
high/low — see `xauusd_scalper/engine.py` for the exact fill and cost model
(spread applied on both entry and exit, commission per lot, stop-loss assumed
to trigger first if a single bar's range would hit both).

## Install

```bash
cd xauusd_scalper
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

## Quick start

1. Generate synthetic 1-min XAUUSD-like data to try the bot end-to-end
   (skip this once you have real historical data — see "Bring your own data"
   below):

   ```bash
   python -m xauusd_scalper.cli generate-sample --out sample_data/xauusd_1min_sample.csv --days 10
   ```

2. Run a backtest over it:

   ```bash
   python -m xauusd_scalper.cli backtest --data sample_data/xauusd_1min_sample.csv --output-dir output/backtest
   ```

   Prints a summary (win rate, profit factor, net P&L, max drawdown, ...) and
   writes `trades.csv` + `equity_curve.csv` to the output directory.

3. Run paper trading — same engine, but streams the CSV bar by bar with
   live-style console output (useful for watching the strategy trade in
   "real time" before pointing it at an actual live feed):

   ```bash
   python -m xauusd_scalper.cli paper --data sample_data/xauusd_1min_sample.csv --speed 0
   ```

   `--speed N` sleeps N seconds between bars if you want it to feel real-time
   (e.g. `--speed 1` ≈ roughly bar-by-bar as it would arrive live once you
   wire in a real feed).

## Bring your own data

Point `--data` at any CSV with a datetime column (`datetime`/`timestamp`/
`date`/`time`) and `open`/`high`/`low`/`close` columns (`volume` optional),
1-minute XAUUSD candles, e.g. exported from your broker/platform or a market
data provider. Column names are matched case-insensitively.

If your source data is a bid/ask JSON feed instead (records shaped like
`{"ts": <unix seconds>, "o","h","l","c": <bid OHLC>, "ao","ah","al","ac":
<ask OHLC>, "v": <volume>}`, optionally gzip-compressed), convert it first:

```bash
python -m xauusd_scalper.cli import-json --input xauusd_2025.json.gz --out data/xauusd_2025_1min.csv
```

This writes mid-price OHLCV (average of bid/ask) and also prints the actual
observed spread (mean/median/p95) from the data — pass that as `--spread` to
`backtest`/`paper` instead of the $0.20 default for a realistic cost model.

## Tuning

Key parameters, overridable via CLI flags or a `--config config.json` file
(see `xauusd_scalper/config.py` for the full list):

```bash
python -m xauusd_scalper.cli backtest \
  --data sample_data/xauusd_1min_sample.csv \
  --fast-ema 9 --slow-ema 21 \
  --atr-sl-mult 1.5 --atr-tp-mult 2.5 \
  --balance 10000 --risk-pct 0.5 --spread 0.20
```

```json
// config.json
{
  "strategy": {"fast_ema": 9, "slow_ema": 21, "atr_sl_mult": 1.5, "atr_tp_mult": 2.5},
  "risk": {"initial_balance": 10000, "risk_per_trade_pct": 0.5, "spread": 0.20}
}
```

## Tests

```bash
pytest
```

## Project layout

```
xauusd_scalper/
  config.py      strategy + risk dataclasses, JSON load/save
  indicators.py  EMA, Wilder RSI, Wilder ATR
  data.py        CSV loader + synthetic sample-data generator
  strategy.py    signal generation (EMA crossover + RSI + session filter)
  risk.py        position sizing (risk % -> lot size)
  engine.py       bar-by-bar simulation core (shared by backtest + paper)
  metrics.py     win rate / profit factor / drawdown / etc.
  backtest.py    full-run backtest, writes trades.csv + equity_curve.csv
  paper.py       paced, live-style streaming run over the same engine
  cli.py         generate-sample / backtest / paper subcommands
tests/
sample_data/     generated CSVs land here by default
```

## Extending to live/demo execution

This project deliberately stops at paper trading. To go further you'd add a
broker adapter (e.g. MetaTrader5, OANDA, a CFD/FX broker's REST API) behind
a small "place order / get live candles" interface, and swap `paper.py`'s
CSV iterator for that live feed — the strategy, risk sizing, and signal logic
here wouldn't need to change. Do that only on a demo account first.
